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

# 核心控制：精准捕获青龙面板的开关宏逻辑
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
ACTOR_RELATION_MAP = {}

# ==================== 常量定义 ====================
ZERO_WIDTH_CHARS = ['\u200c', '\u200b', '\u200d', '\ufeff']
NFO_TAG_TYPES = ['tag', 'genre']


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
    if num:
        cid = num.group(1).replace("<![CDATA[", "").replace("]]>", "").strip()
        if cid:
            return cid.upper()

    title = re.search(r'<title>(.*?)</title>', content, re.DOTALL)
    if title:
        tt = title.group(1).replace("<![CDATA[", "").replace("]]>", "").strip()
        parts = tt.split(' ', 1)
        if parts and (parts[0] and ('-' in parts[0] or re.match(r'^[A-Za-z0-9]+$', parts[0]))):
            return parts[0].upper()

    return os.path.splitext(basename)[0].upper()


def clean_zero_width_chars(text):
    """清洗零宽字符"""
    for char in ZERO_WIDTH_CHARS:
        text = text.replace(char, '')
    return text.strip()


def extract_cabin_data(xml_line):
    """从XML行中提取演员数据舱"""
    t_match = re.search(f'{XML_ATTR}\\s*=\\s*["\'](.*?)["\']', xml_line)
    if not t_match:
        return None
    target_name = t_match.group(1).strip()

    jp_res = re.search(r'jp=["\'](.*?)["\']', xml_line)
    tw_res = re.search(r'zh_tw=["\'](.*?)["\']', xml_line)
    cn_res = re.search(r'zh_cn=["\'](.*?)["\']', xml_line)
    kw_res = re.search(r'keyword=["\'](.*?)["\']', xml_line)

    return {
        "jp": jp_res.group(1).strip() if jp_res else "",
        "zh_tw": tw_res.group(1).strip() if tw_res else "",
        "zh_cn": cn_res.group(1).strip() if cn_res else "",
        "keywords": [k.strip() for k in kw_res.group(1).split(',') if k.strip()] if kw_res else [],
        "target_name": target_name
    }


def is_name_matched(name, cabin):
    """五维刚性全等匹配检测"""
    if name == cabin["jp"]:
        return True
    if name == cabin["zh_tw"]:
        return True
    if name == cabin["zh_cn"]:
        return True
    if name in cabin["keywords"]:
        return True
    return False


def get_all_aliases(cabin):
    """获取演员的所有别名（去重，排除目标名）"""
    raw_aliases = set([cabin["jp"], cabin["zh_tw"], cabin["zh_cn"]] + cabin["keywords"])
    final_name = cabin["target_name"]
    # 使用列表推导式，安全过滤空值和目标名
    return [a for a in raw_aliases if a and a != final_name]


def process_tag_replacements(content, cid, nfo_name, cabin, effectively_changed_old_names):
    """处理tag和genre的替换"""
    global TOTAL_GENRE_COUNTS, TOTAL_TAG_COUNTS

    final_name = cabin["target_name"]
    old_aliases = get_all_aliases(cabin)
    tag_changed = False

    for old_val in old_aliases:
        for t_type in NFO_TAG_TYPES:
            # 普通标签
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

            # CDATA标签
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

    return content, tag_changed


def process_actor_blocks(content, matched_cabins_map, effectively_changed_old_names, cid):
    """处理actor块内的替换"""
    if "<actor>" not in content or not matched_cabins_map:
        return content, False

    new_content = content
    actor_changed = False

    for block in re.findall(r'<actor>.*?</actor>', content, re.DOTALL):
        up_bk = block
        block_modified = False

        for nfo_name, cabin in matched_cabins_map.items():
            final_name = cabin["target_name"]
            if nfo_name == final_name:
                continue

            old_aliases = get_all_aliases(cabin)

            for old_val in old_aliases:
                # 执行替换并记录是否真的有变化
                old_name_tag = f"<name>{old_val}</name>"
                new_name_tag = f"<name>{final_name}</name>"
                if old_name_tag in up_bk:
                    up_bk = up_bk.replace(old_name_tag, new_name_tag)
                    block_modified = True

                old_cdata_tag = f"<name><![CDATA[{old_val}]]></name>"
                new_cdata_tag = f"<name><![CDATA[{final_name}]]></name>"
                if old_cdata_tag in up_bk:
                    up_bk = up_bk.replace(old_cdata_tag, new_cdata_tag)
                    block_modified = True

        if up_bk != block:
            new_content = new_content.replace(block, up_bk)
            actor_changed = True
            # 只有真正执行了替换的演员才记录
            if block_modified:
                for nfo_name, cabin in matched_cabins_map.items():
                    final_name = cabin["target_name"]
                    if nfo_name != final_name:
                        # 检查这个演员是否真的在这个块中被替换了
                        if f"<name>{final_name}</name>" in up_bk:
                            effectively_changed_old_names.add(nfo_name)

    if actor_changed:
        print(f"    🔄 [更名完成] 番号 [{cid}] 成功执行了演员包厢的正向多马甲大置换！")

    return new_content, actor_changed


def process_nfo_actor(nfo_path, xml_lines_pool, targets):
    """处理单个NFO文件，执行演员名称转换"""
    global TOTAL_MODIFIED_NFOS, TOTAL_CIDS_SET, TOTAL_CHANGED_ACTORS

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

        # 零宽字符清洗 + 五维匹配
        for raw_name in set(nfo_raw_names + cdata_names):
            name_clean = clean_zero_width_chars(raw_name)
            if "<!" in name_clean or "]]>" in name_clean or not name_clean:
                continue

            for xml_line in xml_lines_pool:
                if (f'"{name_clean}"' in xml_line or
                    f"'{name_clean}'" in xml_line or
                    name_clean in xml_line):

                    cabin = extract_cabin_data(xml_line)
                    if not cabin:
                        continue

                    if not is_name_matched(name_clean, cabin):
                        continue

                    # 白名单过滤
                    if not IS_AUTO_CONVERT and targets:
                        all_possibilities = [cabin["jp"], cabin["zh_tw"], cabin["zh_cn"]] + cabin["keywords"]
                        if not any(t in all_possibilities for t in targets):
                            continue

                    matched_cabins_map[name_clean] = cabin
                    break

        if not matched_cabins_map:
            return False

        print(f"🕵️ 番号 [{cid}] 成功锁定更名序列：{list(matched_cabins_map.keys())}")

        # 初始化修改追踪
        tag_changed = False
        actor_changed = False
        effectively_changed_old_names = set()

        # 处理tag和genre替换
        for nfo_name, cabin in matched_cabins_map.items():
            if nfo_name == cabin["target_name"]:
                continue  # 已合规，熔断跳过
            content, changed = process_tag_replacements(
                content, cid, nfo_name, cabin, effectively_changed_old_names
            )
            if changed:
                tag_changed = True

        # 处理actor块替换
        content, actor_changed = process_actor_blocks(
            content, matched_cabins_map, effectively_changed_old_names, cid
        )

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
    """发送通知 - 增强版，支持降级方案"""
    # 尝试多种可能的路径
    paths = [
        '/ql/data/scripts',
        '/ql/scripts',
        '/ql/repo/scripts',
        '/ql/scripts/sendNotify',
        '/ql/data/scripts/sendNotify'
    ]

    for p in paths:
        if os.path.exists(os.path.join(p, 'sendNotify.py')) and p not in sys.path:
            sys.path.append(p)

    try:
        import sendNotify
        sendNotify.send(title, content)
        print("✅ 通知发送成功")
    except Exception as err:
        # 降级方案：尝试从sendNotify导入send
        try:
            from sendNotify import send
            send(title, content)
            print("✅ 通知发送成功（降级方案）")
        except Exception as e2:
            # 最终降级：只打印到控制台
            print(f"⚠️ 通知发送失败，但主流程已完成")
            print(f"📱 通知内容预览:\n{content}")
            print(f"🎉 [发信网络网关强制吸收拦截] 回执: {err} | {e2}")


# ==================== 主函数 ====================
def main():
    """主函数入口"""
    # 处理白名单
    if not WHITE_LIST_ENV or WHITE_LIST_ENV.lower() == "none":
        targets = []
    else:
        targets = [n.strip() for n in re.split(r'[\s,，]+', WHITE_LIST_ENV) if n.strip()]

    print("==========================================")
    print("🎬 演员显示语言修改工具")
    print(f"📅 目标语言: {LANG_CHOICE}")
    print(f"⚙️  运行模式: {MODE_NAME}")
    print("==========================================")

    # 检查路径
    if not os.path.exists(MEDIA_DIR):
        print(f"❌ 挂载路径不存在: {MEDIA_DIR}")
        return

    # 检查是否为真空模式
    is_pure_vacuum = (not IS_AUTO_CONVERT and not targets)
    if is_pure_vacuum:
        print("ℹ️  运行提示：网页白名单当前为静态 none。自动挂起退出纯保洁状态。")
        return

    # 加载映射表
    xml_lines_pool = load_actor_cabins_raw()
    if not xml_lines_pool:
        print("❌ 映射表为空，无法继续执行")
        return

    print(f"🚀 启动全盘拉网式快速扫描改写...")
    print(f"📂 扫描路径：{MEDIA_DIR}")
    print(f"🚀 运行模式：开启【{MODE_NAME}模式】！")

    scanned_nfo = 0
    modified_nfo = 0

    # 遍历所有NFO文件
    for r, _, files in os.walk(MEDIA_DIR):
        for f in files:
            if f.lower().endswith('.nfo'):
                scanned_nfo += 1
                if process_nfo_actor(os.path.join(r, f), xml_lines_pool, targets):
                    modified_nfo += 1

    # 输出统计信息
    print("==========================================")
    print(f"📊 扫描结束！共核对 {scanned_nfo} 个 NFO，更新了 {modified_nfo} 个文件。")
    print("==========================================")

    # 控制台审计台账总结
    if TOTAL_MODIFIED_NFOS > 0 and TOTAL_CHANGED_ACTORS:
        print("\n==================================================================")
        print("📊 【本次运行核心施工审计总结报告】")
        print("==================================================================")
        print(f"👤 本次实际更名转换的演员及其关联番号明细 (共 {len(TOTAL_CHANGED_ACTORS)} 位)：")

        for rel_key in sorted(ACTOR_RELATION_MAP.keys(), key=lambda x: x[1]):
            old_nfo_name, final_real_name = rel_key
            associated_cids = sorted(ACTOR_RELATION_MAP[rel_key])
            print(f"   ├── 👤 {old_nfo_name} ➔ {final_real_name}")
            print(f"   │    └── 🎬 关联番号: {', '.join(associated_cids)}")

        print("==================================================================\n")

        # 发送手机通知（简洁版）
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
