# -*- coding: utf-8 -*-
# ql author: actor_cleaner
# ql name: 🎬 演员显示语言修改
# ql cron: 0 0 2 * * *
# ql desc: 自动遍历全盘NFO，根据五维立体交叉核对雷达将演员、Tag、Genre规范化为指定语系
# ==================== 🛠️ 青龙环境变量配置指南 ====================
# 1. MEDIA_DIR        : 媒体库挂载路径。  [默认值: /movies]
# 2. ACTOR_LANG       : 转换的目标语言。  [可选值: 简体中文 / 繁体中文 / 日文] [默认值: 简体中文]
# 3. AUTO_CONVERT_ALL : 是否开启全量转换。[开关状态: 启用且值为 on ➔ 全部演员 / 禁用或非 on ➔ 指定演员]
# 4. ACTOR_WHITE_LIST : 定向更名演员名单。[可选值: 老师名字(英文逗号分隔) / none (静默挂起退出)] [默认值: none]
# 5. ACTOR_XML_PATH   : 映射表文件路径。  [默认值: 脚本同目录下的 actor-mapping.xml]
# =================================================================

import os
import re
import sys

# ==================== 环境变量读取 ====================
MEDIA_DIR = os.getenv("MEDIA_DIR", "/movies").strip()
LANG_CHOICE = os.getenv("ACTOR_LANG", "简体中文").strip()

AUTO_CONVERT_ENV = os.getenv("AUTO_CONVERT_ALL", "").strip().lower()
IS_AUTO_CONVERT = (AUTO_CONVERT_ENV == "on")
MODE_NAME = "全部演员" if IS_AUTO_CONVERT else "指定演员"

WHITE_LIST_ENV = os.getenv("ACTOR_WHITE_LIST", "none").strip()
ACTOR_XML_PATH = os.getenv("ACTOR_XML_PATH", os.path.join(os.path.dirname(__file__), "actor-mapping.xml")).strip()

LANG_MAP = {"简体中文": "zh_cn", "繁体中文": "zh_tw", "日文": "jp"}
XML_ATTR = LANG_MAP.get(LANG_CHOICE, "zh_cn")

TOTAL_CHANGED_ACTORS = set()
TOTAL_MODIFIED_NFOS = 0
TOTAL_GENRE_COUNTS = 0
TOTAL_TAG_COUNTS = 0
TOTAL_CIDS_SET = set()

ACTOR_RELATION_MAP = {}

# 零宽字符常量（内联使用，避免函数调用开销）
ZERO_CHARS = ['\u200c', '\u200b', '\u200d', '\ufeff']


def load_actor_cabins_raw():
    lines_pool = []
    if not os.path.exists(ACTOR_XML_PATH):
        print("❌ 错误：actor-mapping.xml 路径不存在，请检查配置！")
        return lines_pool
    try:
        with open(ACTOR_XML_PATH, "r", encoding="utf-8", errors="ignore") as f:
            for line_raw in f:
                line = line_raw.strip()
                if line and ("<a " in line or "<a\t" in line):
                    lines_pool.append(line)
        print(f"📖 映射表载入成功：全量读取到 【{len(lines_pool)}】 条演员名录大字典数据。")
    except Exception as e:
        print(f"❌ 读取映射表失败: {e}")
    return lines_pool


def get_cid(content, basename):
    num = re.search(r'<num>(.*?)</num>', content, re.DOTALL)
    if num and num.group(1).replace("<![CDATA[", "").replace("]]>", "").strip():
        return num.group(1).replace("<![CDATA[", "").replace("]]>", "").strip().upper()
    title = re.search(r'<title>(.*?)</title>', content, re.DOTALL)
    if title:
        tt = title.group(1).replace("<![CDATA[", "").replace("]]>", "").strip().split(' ', 1)
        if '-' in tt or re.match(r'^[A-Za-z0-9]+$', tt):
            return tt.upper()
    return os.path.splitext(basename)[0].upper()


def process_nfo_actor(nfo_path, xml_lines_pool, targets):
    global TOTAL_MODIFIED_NFOS, TOTAL_GENRE_COUNTS, TOTAL_TAG_COUNTS

    if not os.path.exists(nfo_path):
        return False

    try:
        with open(nfo_path, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()

        orig = content
        cid = get_cid(content, os.path.basename(nfo_path))

        nfo_raw_names = re.findall(r'<name>(.*?)</name>', content)
        cdata_names = re.findall(r'<name>\s*<!\[CDATA\[(.*?)\]\]>\s*</name>', content)

        matched_cabins_map = {}

        # 【性能优化】使用局部变量引用，减少属性查找
        xml_lines = xml_lines_pool
        attr = XML_ATTR
        is_auto = IS_AUTO_CONVERT
        targets_local = targets

        for raw_name in set(nfo_raw_names + cdata_names):
            # 内联零宽字符清洗，避免函数调用开销
            name_clean = raw_name.replace('\u200c', '').replace('\u200b', '').replace('\u200d', '').replace('\ufeff', '').strip()
            if "<!" in name_clean or "]]>" in name_clean or not name_clean:
                continue

            for xml_line in xml_lines:
                if f'"{name_clean}"' in xml_line or f"'{name_clean}'" in xml_line or name_clean in xml_line:
                    t_match = re.search(f'{attr}\\s*=\\s*["\'](.*?)["\']', xml_line)
                    if not t_match:
                        continue
                    t_name = t_match.group(1).strip()

                    jp_res = re.search(r'jp=["\'](.*?)["\']', xml_line)
                    tw_res = re.search(r'zh_tw=["\'](.*?)["\']', xml_line)
                    cn_res = re.search(r'zh_cn=["\'](.*?)["\']', xml_line)
                    kw_res = re.search(r'keyword=["\'](.*?)["\']', xml_line)

                    jp_val = jp_res.group(1).strip() if jp_res else ""
                    tw_val = tw_res.group(1).strip() if tw_res else ""
                    cn_val = cn_res.group(1).strip() if cn_res else ""
                    keywords = [k.strip() for k in kw_res.group(1).split(',') if k.strip()] if kw_res else []

                    # 五维刚性全等匹配
                    is_matched = False
                    if name_clean == jp_val:
                        is_matched = True
                    elif name_clean == tw_val:
                        is_matched = True
                    elif name_clean == cn_val:
                        is_matched = True
                    elif name_clean in keywords:
                        is_matched = True

                    if is_matched:
                        cabin_data = {
                            "jp": jp_val,
                            "zh_tw": tw_val,
                            "zh_cn": cn_val,
                            "keywords": keywords,
                            "target_name": t_name
                        }

                        if not is_auto and targets_local:
                            all_possibilities = [jp_val, tw_val, cn_val] + keywords
                            if not any(t in all_possibilities for t in targets_local):
                                continue

                        matched_cabins_map[name_clean] = cabin_data
                        break

        if not matched_cabins_map:
            return False

        print(f"🕵️ 番号 [{cid}] 成功锁定更名序列：{list(matched_cabins_map.keys())}")

        tag_changed = False
        actor_changed = False
        effectively_changed_old_names = set()

        # ====== 修复点1：安全的别名处理（不使用remove，避免迭代崩溃） ======
        for nfo_name, cabin in matched_cabins_map.items():
            final_name = cabin["target_name"]

            # 如果已合规，熔断跳过
            if nfo_name == final_name:
                continue

            # 【关键修复】使用列表推导式，安全过滤，避免迭代时修改集合
            raw_aliases = set([cabin["jp"], cabin["zh_tw"], cabin["zh_cn"]] + cabin["keywords"])
            old_aliases = [a for a in raw_aliases if a and a != final_name]

            for old_val in old_aliases:
                # 普通标签替换
                for t_type in ['tag', 'genre']:
                    old_tag = f"<{t_type}>{old_val}</{t_type}>"
                    new_tag = f"<{t_type}>{final_name}</{t_type}>"
                    chg_cnt = content.count(old_tag)
                    if chg_cnt > 0:
                        content = content.replace(old_tag, new_tag)
                        tag_changed = True
                        effectively_changed_old_names.add(nfo_name)
                        if t_type == 'genre':
                            TOTAL_GENRE_COUNTS += chg_cnt
                        else:
                            TOTAL_TAG_COUNTS += chg_cnt
                        print(f"    换  [属性同步] 番号 [{cid}] 置换【{t_type}】: {old_val} ➔ {final_name}")

                # CDATA标签替换
                for t_type in ['tag', 'genre']:
                    old_cdata = f"<{t_type}><![CDATA[{old_val}]]></{t_type}>"
                    new_cdata = f"<{t_type}><![CDATA[{final_name}]]></{t_type}>"
                    chg_cdata_cnt = content.count(old_cdata)
                    if chg_cdata_cnt > 0:
                        content = content.replace(old_cdata, new_cdata)
                        tag_changed = True
                        effectively_changed_old_names.add(nfo_name)
                        if t_type == 'genre':
                            TOTAL_GENRE_COUNTS += chg_cdata_cnt
                        else:
                            TOTAL_TAG_COUNTS += chg_cdata_cnt
                        print(f"    换  [属性同步] 番号 [{cid}] 置换【高级{t_type}】: {old_val} ➔ {final_name}")

        # ====== 修复点2：Actor块替换，只记录真正被修改的 ======
        if "<actor>" in content and matched_cabins_map:
            new_content = content
            for block in re.findall(r'<actor>.*?</actor>', content, re.DOTALL):
                up_bk = block
                block_modified = False
                modified_names_in_block = set()

                for nfo_name, cabin in matched_cabins_map.items():
                    final_name = cabin["target_name"]
                    if nfo_name == final_name:
                        continue

                    raw_aliases = set([cabin["jp"], cabin["zh_tw"], cabin["zh_cn"]] + cabin["keywords"])
                    old_aliases = [a for a in raw_aliases if a and a != final_name]

                    for old_val in old_aliases:
                        # 先检查是否真的存在需要替换的内容
                        old_name_tag = f"<name>{old_val}</name>"
                        new_name_tag = f"<name>{final_name}</name>"
                        if old_name_tag in up_bk:
                            up_bk = up_bk.replace(old_name_tag, new_name_tag)
                            block_modified = True
                            modified_names_in_block.add(nfo_name)

                        old_cdata_tag = f"<name><![CDATA[{old_val}]]></name>"
                        new_cdata_tag = f"<name><![CDATA[{final_name}]]></name>"
                        if old_cdata_tag in up_bk:
                            up_bk = up_bk.replace(old_cdata_tag, new_cdata_tag)
                            block_modified = True
                            modified_names_in_block.add(nfo_name)

                if block_modified:
                    new_content = new_content.replace(block, up_bk)
                    actor_changed = True
                    # 只记录在这个块中真正被修改的演员
                    for name in modified_names_in_block:
                        effectively_changed_old_names.add(name)

            if actor_changed:
                content = new_content
                print(f"    🔄 [更名完成] 番号 [{cid}] 成功执行了演员包厢的正向多马甲大置换！")

        # 保存修改
        if (tag_changed or actor_changed) and content != orig:
            with open(nfo_path, "w", encoding="utf-8") as f:
                f.write(re.sub(r'\n\s*\n', '\n', content))

            TOTAL_MODIFIED_NFOS += 1
            TOTAL_CIDS_SET.add(cid)

            # 只记录真正被修改的演员
            for nfo_name in effectively_changed_old_names:
                cabin = matched_cabins_map.get(nfo_name)
                if cabin:
                    final_name = cabin["target_name"]
                    TOTAL_CHANGED_ACTORS.add(final_name)
                    rel_key = (nfo_name, final_name)
                    ACTOR_RELATION_MAP.setdefault(rel_key, set()).add(cid)

            return True

    except Exception as e:
        print(f"    ⚠️ 遇到未知控制异常: {e}")

    return False


def send_notify(title, content):
    # 只尝试最常用的路径，减少循环开销
    for p in ['/ql/data/scripts', '/ql/scripts', '/ql/repo/scripts']:
        if os.path.exists(os.path.join(p, 'sendNotify.py')):
            if p not in sys.path:
                sys.path.append(p)
            break

    try:
        from sendNotify import send
        send(title, content)
    except Exception as err:
        # 降级方案
        try:
            import importlib.util
            for p in ['/ql/data/scripts', '/ql/scripts', '/ql/repo/scripts']:
                nf_path = os.path.join(p, 'sendNotify.py')
                if os.path.exists(nf_path):
                    spec = importlib.util.spec_from_file_location("sendNotify", nf_path)
                    sn = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(sn)
                    sn.send(title, content)
                    return
        except Exception as e2:
            print(f"⚠️ 通知发送失败: {err} | {e2}")
            print(f"📱 通知内容:\n{content}")


def main():
    if not WHITE_LIST_ENV or WHITE_LIST_ENV.lower() == "none":
        targets = []
    else:
        targets = [n.strip() for n in re.split(r'[\s,，]+', WHITE_LIST_ENV) if n.strip()]

    print("==========================================")
    print("🎬 演员显示语言修改工具")
    print("==========================================")

    if not os.path.exists(MEDIA_DIR):
        print(f"❌ 挂载路径不存在: {MEDIA_DIR}")
        return

    is_pure_vacuum = (not IS_AUTO_CONVERT and not targets)
    if is_pure_vacuum:
        print("ℹ️  运行提示：网页白名单当前为静态 none。自动挂起退出纯保洁状态。")
        return

    xml_lines_pool = load_actor_cabins_raw()
    if not xml_lines_pool:
        print("❌ 映射表为空，无法继续执行")
        return

    print(f"🚀 启动全盘拉网式快速扫描改写...")
    print(f"📂 扫描路径：{MEDIA_DIR}")
    print(f"🚀 运行模式：开启【{MODE_NAME}模式】！")

    scanned_nfo = 0
    modified_nfo = 0

    for r, _, files in os.walk(MEDIA_DIR):
        for f in files:
            if f.lower().endswith('.nfo'):
                scanned_nfo += 1
                if process_nfo_actor(os.path.join(r, f), xml_lines_pool, targets):
                    modified_nfo += 1

    print("==========================================")
    print(f"📊 扫描结束！共核对 {scanned_nfo} 个 NFO，更新了 {modified_nfo} 个文件。")
    print("==========================================")

    if TOTAL_MODIFIED_NFOS > 0 and TOTAL_CHANGED_ACTORS:
        print("\n==================================================================")
        print("📊 【本次运行核心施工审计总结报告】")
        print("==================================================================")
        print(f"👤 本次实际更名转换的演员及其关联番号明细 (共 {len(TOTAL_CHANGED_ACTORS)} 位)：")

        for rel_key in sorted(list(ACTOR_RELATION_MAP.keys()), key=lambda x: x[1]):
            old_nfo_name, final_real_name = rel_key
            associated_cids = sorted(list(ACTOR_RELATION_MAP[rel_key]))
            print(f"   ├── 👤 {old_nfo_name} ➔ {final_real_name}")
            print(f"   │    └── 🎬 关联番号: {', '.join(associated_cids)}")

        print("==================================================================\n")

        title_notify = "🎬 演员显示语言修改"
        content = f"🗣️ 语言语系：【{LANG_CHOICE}】"
        content += f"\n⚙️ 修改模式：【{MODE_NAME}】"
        content += f"\n📊 修改结果：【更新nfo文件: {TOTAL_MODIFIED_NFOS} 个，关联番号: {len(TOTAL_CIDS_SET)} 个】"
        content += f"\n  ├── 演员: {len(TOTAL_CHANGED_ACTORS)} 位"
        content += f"\n  ├── 类型: {TOTAL_GENRE_COUNTS} 个"
        content += f"\n  └── 标签: {TOTAL_TAG_COUNTS} 个"
        send_notify(title_notify, content.strip())
    else:
        print("ℹ️  终极提示：本次扫描未发现任何实质性文本变动，或测试集NFO早已完全对齐规范。已安全跳过手机通知派发。")


if __name__ == "__main__":
    main()
