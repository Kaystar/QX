# -*- coding: utf-8 -*-
# ql author: actor_converter
# ql name: 🌐 演员显示语言修改
# ql cron: 0 2 * * *
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

# 核心控制：精准捕获青龙面板的开关宏逻辑，只有启用且值为 on 时才激活全量模式
AUTO_CONVERT_ENV = os.getenv("AUTO_CONVERT_ALL", "").strip().lower()
IS_AUTO_CONVERT = (AUTO_CONVERT_ENV == "on")
MODE_NAME = "全部演员" if IS_AUTO_CONVERT else "指定演员"

WHITE_LIST_ENV = os.getenv("ACTOR_WHITE_LIST", "none").strip()
ACTOR_XML_PATH = os.getenv(
    "ACTOR_XML_PATH",
    os.path.join(os.path.dirname(__file__), "actor-mapping.xml")
).strip()

# ==================== 全局配置 ====================
LANG_MAP = {
    "简体中文": "zh_cn",
    "繁体中文": "zh_tw",
    "日文": "jp"
}
XML_ATTR = LANG_MAP.get(LANG_CHOICE, "zh_cn")

# ==================== 全局统计变量 ====================
TOTAL_CHANGED_ACTORS = set()
TOTAL_MODIFIED_NFOS = 0
TOTAL_GENRE_COUNTS = 0
TOTAL_TAG_COUNTS = 0
TOTAL_CIDS_SET = set()

# 全局精细对照容器：储存 【(NFO旧名字, 最终目标规范名) -> 属于这个变更对应且被修改成功的番号集合】
ACTOR_RELATION_MAP = {}


# ==================== 工具函数 ====================
def load_actor_cabins_raw():
    """加载演员映射表XML文件，提取所有演员条目"""
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
    """从NFO内容中提取番号(CID)"""
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
    """处理单个NFO文件，执行演员名称转换"""
    global TOTAL_MODIFIED_NFOS, TOTAL_GENRE_COUNTS, TOTAL_TAG_COUNTS

    if not os.path.exists(nfo_path):
        return False

    try:
        with open(nfo_path, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()

        orig = content
        cid = get_cid(content, os.path.basename(nfo_path))

        # 提取NFO中所有演员名字
        nfo_raw_names = re.findall(r'<name>(.*?)</name>', content)
        cdata_names = re.findall(r'<name>\s*<!\[CDATA\[(.*?)\]\]>\s*</name>', content)

        # 核心映射容器：储存 NFO 旧名字到其对应的完整属性舱的映射关系
        matched_cabins_map = {}

        # 终极去噪层：在最前端用 replace 暴力物理清洗、蒸发所有隐藏的 Unicode 零宽字符
        for raw_name in set(nfo_raw_names + cdata_names):
            name_clean = raw_name.replace('\u200c', '').replace('\u200b', '').replace('\u200d', '').replace('\ufeff', '').strip()
            if "<!" in name_clean or "]]>" in name_clean or not name_clean:
                continue

            for xml_line in xml_lines_pool:
                # 模糊嗅探：只要 NFO 演员名躺在这行 XML 里的任意一处属性或马甲中
                if (f'"{name_clean}"' in xml_line or
                    f"'{name_clean}'" in xml_line or
                    name_clean in xml_line):

                    # 提取整条演员信息的全部核心语系字段
                    t_match = re.search(f'{XML_ATTR}\\s*=\\s*["\'](.*?)["\']', xml_line)
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

                    # 五维刚性全等硬锁，彻底铲除混淆幽灵
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

                        if not IS_AUTO_CONVERT and targets:
                            all_possibilities = [jp_val, tw_val, cn_val] + keywords
                            if not any(t in all_possibilities for t in targets):
                                continue

                        matched_cabins_map[name_clean] = cabin_data
                        break

        # 隔离硬锁：将修改标志位精细化拆分为独立的原子触发器
        tag_changed = False
        actor_changed = False

        # 记录真正发生过文字清洗置换的有效旧名字集合
        effectively_changed_old_names = set()

        # 终极正向修改：用 NFO 里所有可能的旧马甲去替换为最终目标名字
        for nfo_name, cabin in matched_cabins_map.items():
            final_name = cabin["target_name"]

            # 收集此人名下在映射表中所有可能出现的旧别名
            old_aliases = set([cabin["jp"], cabin["zh_tw"], cabin["zh_cn"]] + cabin["keywords"])

            for old_val in old_aliases:
                if not old_val or old_val == final_name:
                    continue

                # 正向擦除 NFO 内部普通的 tag 和 genre 标签
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

                # 正向擦除 NFO 内部带有 CDATA 属性的高级变体标签
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

        # 演员块定界锁，1:1 像素级精准替换
        if "<actor>" in content and matched_cabins_map:
            new_content = content
            for block in re.findall(r'<actor>.*?</actor>', content, re.DOTALL):
                up_bk = block
                for nfo_name, cabin in matched_cabins_map.items():
                    final_name = cabin["target_name"]
                    old_aliases = set([cabin["jp"], cabin["zh_tw"], cabin["zh_cn"]] + cabin["keywords"])
                    for old_val in old_aliases:
                        if not old_val or old_val == final_name:
                            continue
                        up_bk = up_bk.replace(
                            f"<name>{old_val}</name>",
                            f"<name>{final_name}</name>"
                        )
                        up_bk = up_bk.replace(
                            f"<name><![CDATA[{old_val}]]></name>",
                            f"<name><![CDATA[{final_name}]]></name>"
                        )
                if up_bk != block:
                    new_content = new_content.replace(block, up_bk)
                    actor_changed = True
                    for tmp_name, tmp_cabin in matched_cabins_map.items():
                        if (f"<name>{tmp_cabin['target_name']}</name>" in up_bk or
                            f"<![CDATA[{tmp_cabin['target_name']}]]>" in up_bk):
                            effectively_changed_old_names.add(tmp_name)
            if actor_changed:
                content = new_content
                print(f"    🔄 [更名完成] 番号 [{cid}] 成功执行了演员包厢的正向多马甲大置换！")

        # 三向记账去重锁：只有内容变动才进池
        if (tag_changed or actor_changed) and content != orig:
            with open(nfo_path, "w", encoding="utf-8") as f:
                f.write(re.sub(r'\n\s*\n', '\n', content))
            TOTAL_MODIFIED_NFOS += 1
            TOTAL_CIDS_SET.add(cid)

            # 精准对账：只把那些真正被动过手术的演员大名塞进控制台审计清单和去重人头池
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
    """官方原生防爆网关：强行采用刚性动态挂载，100% 根除外部变量抛出的未定义 response 核弹"""
    for p in ['/ql/data/scripts', '/ql/scripts', '/ql/repo/scripts', '/ql/scripts/sendNotify']:
        if p not in sys.path:
            sys.path.append(p)

    try:
        import sendNotify
        sendNotify.send(title, content)
    except Exception as err:
        print(f"🎉 [发信网络网关强制吸收拦截] 已经无声切断模块内部流产，保障主进程通畅。回执: {err}")


# ==================== 主函数 ====================
def main():
    # 处理白名单
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

    # 控制台审计台账总结
    if TOTAL_MODIFIED_NFOS > 0 and TOTAL_CHANGED_ACTORS:
        # 核心修正：大长串详细因果对照台账明细，100% 留守在青龙控制台（Log）里打印！绝对不发手机！
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

        # 终极绝杀：发送到手机的 content 卡片内容彻底脱水降维，只保留 5 行极简总账概要！
        title_notify = "🌐 演员显示语言修改"
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
