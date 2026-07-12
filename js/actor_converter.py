# -*- coding: utf-8 -*-
# ql author: actor_converter
# ql name: 🌐 演员显示语言修改
# ql cron: 0 2 * * *
# ql desc: 引入四重内存O(1)快取哈希表，暴力终结全库高压挂起卡死，极速汉化更名
# ==================== 🛠️ 青龙环境变量配置指南 ====================
# 1. MEDIA_DIR        : 媒体库挂载路径.  [默认值: /movies]
# 2. ACTOR_LANG       : 转换的目标语言.  [可选值: 简体中文 / 繁体中文 / 日文] [默认值: 简体中文]
# 3. AUTO_CONVERT_ALL : 是否开启全量转换。[开关状态: 启用且值为 on ➔ 全部演员 / 禁用或非 on ➔ 指定演员]
# 4. ACTOR_WHITE_LIST : 定向更名演员名单。[可选值: 老师名字(英文逗号分隔) / none (静默挂起退出)] [默认值: none]
# 5. ACTOR_XML_PATH   : 映射表文件路径.  [默认值: 脚本同目录下的 actor-mapping.xml]
# =================================================================

import os
import re
import sys

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

# 🌟 四重内存 O(1) 刚性等值快取哈希数据库，彻底绝杀循环海啸
JP_HASH_DB = {}
TW_HASH_DB = {}
CN_HASH_DB = {}
KW_HASH_DB = {}


def build_speed_hash_cache(targets):
    if not os.path.exists(ACTOR_XML_PATH):
        print("❌ 错误：actor-mapping.xml 路径不存在，请检查配置！")
        return 0
    loaded_count = 0
    try:
        with open(ACTOR_XML_PATH, "r", encoding="utf-8", errors="ignore") as f:
            for line_raw in f:
                line = line_raw.strip()
                if not line or not ("<a " in line or "<a\t" in line):
                    continue

                t_match = re.search(f'{XML_ATTR}\\s*=\\s*["\'](.*?)["\']', line)
                if not t_match:
                    continue
                t_name = t_match.group(1).strip()

                jp_res = re.search(r'jp=["\'](.*?)["\']', line)
                tw_res = re.search(r'zh_tw=["\'](.*?)["\']', line)
                cn_res = re.search(r'zh_cn=["\'](.*?)["\']', line)
                kw_res = re.search(r'keyword=["\'](.*?)["\']', line)

                jp_val = jp_res.group(1).strip() if jp_res else ""
                tw_val = tw_res.group(1).strip() if tw_res else ""
                cn_val = cn_res.group(1).strip() if cn_res else ""
                keywords = [k.strip() for k in kw_res.group(1).split(',') if k.strip()] if kw_res else []

                cabin_data = {
                    "jp": jp_val,
                    "zh_tw": tw_val,
                    "zh_cn": cn_val,
                    "keywords": keywords,
                    "target_name": t_name
                }

                if not IS_AUTO_CONVERT and targets:
                    all_possibilities = [jp_val, tw_val, cn_val] + keywords
                    if not any(t in all_possibilities for t in targets):
                        continue

                if jp_val:
                    JP_HASH_DB[jp_val] = cabin_data
                if tw_val:
                    TW_HASH_DB[tw_val] = cabin_data
                if cn_val:
                    CN_HASH_DB[cn_val] = cabin_data
                for kw in keywords:
                    if kw:
                        KW_HASH_DB[kw] = cabin_data
                loaded_count += 1
        print(f"📖 映射表载入成功：全量读取到 【{loaded_count}】 条符合工况的独立演员身份数据舱。")
    except Exception as e:
        print(f"❌ 读取映射表失败: {e}")
    return loaded_count


def get_cid(content, basename):
    num = re.search(r'<num>(.*?)</num>', content, re.DOTALL)
    if num and num.group(1).replace("<![CDATA[", "").replace("]]>", "").strip():
        return num.group(1).replace("<![CDATA[", "").replace("]]>", "").strip().upper()
    title = re.search(r'<title>(.*?)</title>', content, re.DOTALL)
    if title:
        tt = title.group(1).replace("<![CDATA[", "").replace("]]>", "").strip().split(' ', 1)
        if '-' in tt or re.match(r'^[A-Za-z0-9]+$', tt):
            return tt.upper()
    return os.path.splitext(basename).upper()


def process_nfo_actor(nfo_path):
    global TOTAL_MODIFIED_NFOS, TOTAL_GENRE_COUNTS, TOTAL_TAG_COUNTS
    if not os.path.exists(nfo_path):
        return False
    try:
        with open(nfo_path, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()
        orig, cid = content, get_cid(content, os.path.basename(nfo_path))

        nfo_raw_names = re.findall(r'<name>(.*?)</name>', content)
        cdata_names = re.findall(r'<name>\s*<!\[CDATA\[(.*?)\]\]>\s*</name>', content)

        matched_cabins_map = {}
        # 零宽不连字幽灵清除防火墙完好在位
        for raw_name in set(nfo_raw_names + cdata_names):
            name_clean = raw_name.replace('\u200c', '').replace('\u200b', '').replace('\u200d', '').replace('\ufeff',
                                                                                                           '').strip()
            if "<!" in name_clean or "]]>" in name_clean or not name_clean:
                continue

            # 🌟 0毫秒级哈希碰撞，彻底告别假死卡顿
            cabin = None
            if name_clean in JP_HASH_DB:
                cabin = JP_HASH_DB[name_clean]
            elif name_clean in TW_HASH_DB:
                cabin = TW_HASH_DB[name_clean]
            elif name_clean in CN_HASH_DB:
                cabin = CN_HASH_DB[name_clean]
            elif name_clean in KW_HASH_DB:
                cabin = KW_HASH_DB[name_clean]

            if cabin:
                matched_cabins_map[name_clean] = cabin

        tag_changed = False
        actor_changed = False
        effectively_changed_old_names = set()

        for nfo_name, cabin in matched_cabins_map.items():
            final_name = cabin["target_name"]

            # 🌟 顺向隔离熔断硬锁：本来就改对的名字在内存层直接刹车跳过，绝不反向污染
            if nfo_name == final_name:
                continue

            old_aliases = set([cabin["jp"], cabin["zh_tw"], cabin["zh_cn"]] + cabin["keywords"])
            if final_name in old_aliases:
                old_aliases.remove(final_name)

            for old_val in old_aliases:
                if not old_val:
                    continue

                for t_type in ['tag', 'genre']:
                    old_tag = f"<{t_type}>{old_val}</{t_type}>"
                    new_tag = f"<{t_type}>{final_name}</{t_type}>"
                    chg_cnt = content.count(old_tag)
                    if chg_cnt > 0:
                        content, tag_changed = content.replace(old_tag, new_tag), True
                        effectively_changed_old_names.add(nfo_name)
                        if t_type == 'genre':
                            TOTAL_GENRE_COUNTS += chg_cnt
                        else:
                            TOTAL_TAG_COUNTS += chg_cnt
                        print(f"    换  [属性同步] 番号 [{cid}] 置换【{t_type}】: {old_val} ➔ {final_name}")

                for t_type in ['tag', 'genre']:
                    old_cdata = f"<{t_type}><![CDATA[{old_val}]]></{t_type}>"
                    new_cdata = f"<{t_type}><![CDATA[{final_name}]]></{t_type}>"
                    chg_cdata_cnt = content.count(old_cdata)
                    if chg_cdata_cnt > 0:
                        content, tag_changed = content.replace(old_cdata, new_cdata), True
                        effectively_changed_old_names.add(nfo_name)
                        if t_type == 'genre':
                            TOTAL_GENRE_COUNTS += chg_cdata_cnt
                        else:
                            TOTAL_TAG_COUNTS += chg_cdata_cnt
                        print(f"    换  [属性同步] 番号 [{cid}] 置换【高级{t_type}】: {old_val} ➔ {final_name}")

        if "<actor>" in content and matched_cabins_map:
            new_content = content
            for block in re.findall(r'<actor>.*?</actor>', content, re.DOTALL):
                up_bk = block
                for nfo_name, cabin in matched_cabins_map.items():
                    final_name = cabin["target_name"]
                    if nfo_name == final_name:
                        continue

                    old_aliases = set([cabin["jp"], cabin["zh_tw"], cabin["zh_cn"]] + cabin["keywords"])
                    if final_name in old_aliases:
                        old_aliases.remove(final_name)

                    for old_val in old_aliases:
                        if not old_val:
                            continue
                        up_bk = up_bk.replace(f"<name>{old_val}</name>", f"<name>{final_name}</name>")
                        up_bk = up_bk.replace(f"<name><![CDATA[{old_val}]]></name>",
                                               f"<name><![CDATA[{final_name}]]></name>")
                if up_bk != block:
                    new_content, actor_changed = new_content.replace(block, up_bk), True
                    for tmp_name, tmp_cabin in matched_cabins_map.items():
                        if f"<name>{tmp_cabin['target_name']}</name>" in up_bk or f"<![CDATA[{tmp_cabin['target_name']}]]>" in up_bk:
                            effectively_changed_old_names.add(tmp_name)
            if actor_changed:
                content = new_content
                print(f"    🔄 [更名完成] 番号 [{cid}] 成功执行了演员包厢的正向多马甲大置换！")

        if (tag_changed or actor_changed) and content != orig:
            with open(nfo_path, "w", encoding="utf-8") as f:
                f.write(re.sub(r'\n\s*\n', '\n', content))
            TOTAL_MODIFIED_NFOS += 1
            TOTAL_CIDS_SET.add(cid)

            for nfo_name in effectively_changed_old_names:
                cabin = matched_cabins_map[nfo_name]
                final_name = cabin["target_name"]
                TOTAL_CHANGED_ACTORS.add(final_name)
                rel_key = (nfo_name, final_name)
                ACTOR_RELATION_MAP.setdefault(rel_key, set()).add(cid)
            return True
    except Exception as e:
        print(f"    ⚠️ 遇到未知控制异常: {e}")
    return False


def send_notify(title, content):
    for p in ['/ql/data/scripts', '/ql/scripts', '/ql/repo/scripts', '/ql/scripts/sendNotify']:
        sys.path.append(p) if p not in sys.path else None
    try:
        import sendNotify
        sendNotify.send(title, content)
    except Exception as err:
        print(f"🎉 [发信网络网关异常回执拦截吸纳] 已拦截网络超时，保障主流程。回执: {err}")


def main():
    if not WHITE_LIST_ENV or WHITE_LIST_ENV.lower() == "none":
        targets = []
    else:
        targets = [n.strip() for n in re.split(r'[\s,，]+', WHITE_LIST_ENV) if n.strip()]

    print(f"==========================================\n🎬 演员显示语言修改工具\n==========================================")

    if not os.path.exists(MEDIA_DIR):
        print(f"❌ 挂载路径不存在: {MEDIA_DIR}")
        return

    is_pure_vacuum = (not IS_AUTO_CONVERT and not targets)
    if is_pure_vacuum:
        print("ℹ️  运行提示：网页白名单当前为静态 none。自动挂起退出纯保洁状态。")
        return

    if build_speed_hash_cache(targets) == 0:
        print("ℹ️  运行提示：在当前筛选工况下，内存哈希字典没有捕获到任何有效人头，安全结束退出。")
        return

    print(f"🚀 启动全盘拉网式快速扫描改写...\n📂 扫描路径：{MEDIA_DIR}\n🚀 运行模式：开启【{MODE_NAME}模式】！")

    scanned_nfo, modified_nfo = 0, 0
    for r, _, files in os.walk(MEDIA_DIR):
        for f in files:
            if f.lower().endswith('.nfo'):
                scanned_nfo += 1
                if process_nfo_actor(os.path.join(r, f)):
                    modified_nfo += 1

    print(f"==========================================\n📊 扫描结束！共核对 {scanned_nfo} 个 NFO，更新了 {modified_nfo} 个文件。\n==========================================")

    if TOTAL_MODIFIED_NFOS > 0 and TOTAL_CHANGED_ACTORS:
        # 大明细台账 100% 只留在日志里打印！绝对不发手机！
        print("\n==================================================================")
        print("📊 【本次运行核心施工审计总结报告】")
        print("==================================================================")
        print(f"👤 本次实际更名转换的演员及其关联番号明细 (共 {len(TOTAL_CHANGED_ACTORS)} 位)：")
        for rel_key in sorted(list(ACTOR_RELATION_MAP.keys()), key=lambda x: x):
            old_nfo_name, final_real_name = rel_key
            associated_cids = sorted(list(ACTOR_RELATION_MAP[rel_key]))
            print(f"   ├── 👤 {old_nfo_name} ➔ {final_real_name}")
            print(f"   │    └── 🎬 关联番号: {', '.join(associated_cids)}")
        print("==================================================================\n")

        # 🌟 绝杀订正点：文案100%刚性更正为“演员: X 位”，灰色写实派组件完美刚性垂直像素平齐！
        title_notify = "🌐 演员显示语言修改"
        content = f"🗣️ 语言语系：【{LANG_CHOICE}】"
        content += f"\n⚙️ 修改模式：【{MODE_NAME}】"
        content += f"\n📊 修改结果：【更新nfo文件: {TOTAL_MODIFIED_NFOS} 个，关联番号: {len(TOTAL_CIDS_SET)} 个】"
        content += f"\n  ├── 演员: {len(TOTAL_CHANGED_ACTORS)} 位"
        content += f"\n  ├── 类型: {TOTAL_GENRE_COUNTS} 个"
        content += f"\n  └── 标签: {TOTAL_TAG_COUNTS} 个"
        send_notify(title_notify, content.strip())
    else:
        print("ℹ️  终极提示：本次扫描未发现 any 实质性文本变动，或测试集NFO早已完全对齐规范。已安全跳过手机通知派发。")


if __name__ == "__main__":
    main()
