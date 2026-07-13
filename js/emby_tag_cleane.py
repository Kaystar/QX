#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
================================================================================
脚本名称: Emby NFO 标签精准修正工具
脚本用途: 
  主要用于自动化批量修正 Emby/Jellyfin 媒体库中 NFO 文件的演员(actor)、流派(genre)
  和标签(tag)。特别是针对刮削器误刮、翻译错乱（如将人名错洗为带有职位、日文原文等后缀）
  的场景，提供精准的“原地替换”与“自动瘦身”功能。

核心特性:
  1. 严格模式匹配：只有当本地 NFO 文件中存在完全一致的“原名”时，才会触发修改。
  2. 损坏 XML 强容错：自动拦截并在线修复诸如 `<originalplot>` 标签未闭合等导致的
     `mismatched tag` 严重语法错误，确保脚本不崩溃、不漏洗。
  3. 任务清单自动清理：脚本物理修改成功后，会自动从本地 `modify.list` 清单中剔除已生效
     的规则，未触发的规则继续保留，使清单变成“待处理任务面板”。
  4. 智能通知合并：优先将统计报告与番号清单合并为一条 Telegram 通知发送，只有当字数
     超过电报限制（安全线 3500 字）时，才会自动切片分批投递。

使用说明:
  1. 环境变量配置 (青龙面板/Docker)：
     - MEDIA_DIR: 必填。Emby 媒体目录，多个目录用英文逗号隔开。
                  示例：/media/movies,/media/tv
     - MODIFY_LIST: 选填。若不想用文件，可直接在此环境变量中输入修改规则。
  2. 本地文件配置 (推荐)：
     - 在脚本同级目录下创建一个名为 `modify.list` 的文件。
     - 写入你的修改规则，每行一组，格式为：原名=新名
     - 示例：
       唯果歳製薬会社OL=唯果
       仁美歳OL=仁美
  3. 运行后：
     - 脚本会自动扫描 NFO 并进行原地物理修改。
     - 修改完成后，打开 Emby 后台对受影响的影片点击“刷新元数据”即可看到干净的界面。

cron: 0 4 * * *
new Env('NFO标签精准修正工具');
================================================================================
"""

import os
import re
import xml.etree.ElementTree as ET
from datetime import datetime

# 获取环境变量
MEDIA_DIRS_ENV = os.environ.get("MEDIA_DIR", "")
MODIFY_LIST_ENV = os.environ.get("MODIFY_LIST", "")

# 全局统计数据
stats = {
    "total_nfo": 0,
    "modified_files_count": 0,
    "actor_changes": 0,
    "genre_changes": 0,
    "tag_changes": 0,
    "affected_fanhao": set()  # 记录关联的番号/文件名
}

# 记录哪些规则在此次运行中真正产生了修改
triggered_rules = set()

def parse_modify_list():
    """解析修改映射列表（优先读同级文件，次之读环境变量）"""
    mapping = {}
    current_dir = os.path.dirname(os.path.abspath(__file__))
    file_path = os.path.join(current_dir, "modify.list")
    
    content = ""
    if os.path.exists(file_path):
        print(f"[配置加载] 检测到本地配置文件: {file_path}，正在读取...")
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                content = f.read()
        except Exception as e:
            print(f"[错误] 读取本地 modify.list 失败: {e}")
    else:
        print(f"[配置加载] 未检测到本地 modify.list 文件，尝试读取环境变量 MODIFY_LIST...")
        content = MODIFY_LIST_ENV

    if not content:
        return mapping

    lines = content.strip().split('\n')
    for line in lines:
        if '=' in line:
            kv = line.split('=', 1)
            k = kv[0].strip()
            v = kv[1].strip()
            if k and v:
                mapping[k] = v
    return mapping

def clean_modify_list_file():
    """从本地 modify.list 文件中清理掉已经生效过的规则"""
    current_dir = os.path.dirname(os.path.abspath(__file__))
    file_path = os.path.join(current_dir, "modify.list")
    
    if not os.path.exists(file_path):
        return
        
    if not triggered_rules:
        print("[列表清理] 没有规则被触发修改，无需清理本地文件。")
        return

    print(f"[列表清理] 正在清理已生效的规则...")
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
            
        new_lines = []
        removed_count = 0
        for line in lines:
            if '=' in line:
                k = line.split('=', 1)[0].strip()
                if k in triggered_rules:
                    removed_count += 1
                    continue  # 跳过这一行，即删除
            new_lines.append(line)
            
        with open(file_path, "w", encoding="utf-8") as f:
            f.writelines(new_lines)
            
        print(f"[列表清理] 清理完成，已从 modify.list 中移除 {removed_count} 条已生效规则。")
    except Exception as e:
        print(f"[错误] 清理本地 modify.list 文件失败: {e}")

def clean_xml_string(xml_str):
    """清理并修复非法XML字符，强行自动修复未闭合的 originalplot 标签"""
    illegal_chars_re = re.compile(r'[\x00-\x08\x0B\x0C\x0E-\x1F\x7F-\x84\x86-\x9F]')
    xml_str = illegal_chars_re.sub('', xml_str)
    
    # 修复未转义的 & 符号
    xml_str = re.sub(r'&(?!(amp|lt|gt|quot|apos|#\d+);)', '&amp;', xml_str)
    
    # 【损坏XML修复】如果发现 <originalplot> 后面误用了单边 > 闭合，强行将其规范化并用 CDATA 包裹
    if "<originalplot>" in xml_str and "</originalplot>" not in xml_str:
        xml_str = re.sub(r'<originalplot>([^<]*?)>', r'<originalplot><![CDATA[\1]]></originalplot>', xml_str)
        
    return xml_str

def indent(elem, level=0):
    """辅助函数：保持标准的 XML 缩进换行，防止标签挤在一行"""
    i = "\n" + level * "  "
    if len(elem):
        if not elem.text or not elem.text.strip():
            elem.text = i + "  "
        if not elem.tail or not elem.tail.strip():
            elem.tail = i
        for elem in elem:
            indent(elem, level + 1)
        if not elem.tail or not elem.tail.strip():
            elem.tail = i
    else:
        if level and (not elem.tail or not elem.tail.strip()):
            elem.tail = i

def process_nfo(file_path, mapping):
    """核心修改逻辑"""
    try:
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            raw_content = f.read()
        cleaned_content = clean_xml_string(raw_content)
        root = ET.fromstring(cleaned_content)
    except Exception as e:
        print(f"[跳过破损文件] NFO 语法严重错误: {file_path}, 错误信息: {e}")
        return

    is_file_changed = False
    base_name = os.path.splitext(os.path.basename(file_path))[0]
    
    # 局部临时计数器，写盘成功后才并入全局
    local_actor_changes = 0
    local_genre_changes = 0
    local_tag_changes = 0

    # 1. 严格修改演员节点
    for actor in root.findall('actor'):
        name_node = actor.find('name')
        if name_node is not None and name_node.text:
            orig_name = name_node.text.strip()
            if orig_name in mapping:
                new_name = mapping[orig_name]
                name_node.text = new_name
                local_actor_changes += 1
                triggered_rules.add(orig_name)
                is_file_changed = True
                print(f"  [修改演员] {orig_name} -> {new_name} ({base_name})")

    # 2. 严格修改 genre 节点
    for genre in root.findall('genre'):
        if genre.text:
            orig_genre = genre.text.strip()
            if orig_genre in mapping:
                new_genre = mapping[orig_genre]
                genre.text = new_genre
                local_genre_changes += 1
                triggered_rules.add(orig_genre)
                is_file_changed = True
                print(f"  [修改Genre] {orig_genre} -> {new_genre} ({base_name})")

    # 3. 严格修改 tag 节点
    for tag in root.findall('tag'):
        if tag.text:
            orig_tag = tag.text.strip()
            if orig_tag in mapping:
                new_tag = mapping[orig_tag]
                tag.text = new_tag
                local_tag_changes += 1
                triggered_rules.add(orig_tag)
                is_file_changed = True
                print(f"  [修改Tag] {orig_tag} -> {new_tag} ({base_name})")

    # 如果发生物理修改，回写文件
    if is_file_changed:
        indent(root)
        tree = ET.ElementTree(root)
        
        try:
            tmp_path = file_path + ".tmp"
            tree.write(tmp_path, encoding='utf-8', xml_declaration=True)
            if os.path.exists(file_path):
                os.remove(file_path)
            os.rename(tmp_path, file_path)
            
            # 物理成功写入后，更新全局统计
            stats["modified_files_count"] += 1
            stats["affected_fanhao"].add(base_name)
            stats["actor_changes"] += local_actor_changes
            stats["genre_changes"] += local_genre_changes
            stats["tag_changes"] += local_tag_changes
            
        except Exception as e:
            print(f"[物理写入失败] 文件: {file_path}, 错误: {e}")

def main():
    print(f"=== Emby NFO 标签精准修正开始 ===")
    
    if not MEDIA_DIRS_ENV:
        print("[错误] 未检测到环境变量 MEDIA_DIR。")
        send_notification("Emby标签修正失败", "未检测到环境变量 MEDIA_DIR")
        return

    mapping = parse_modify_list()
    if not mapping:
        print("[错误] 未找到有效的修改映射规则，请检查本地 modify.list 文件或环境变量 MODIFY_LIST。")
        send_notification("Emby标签修正失败", "未找到有效的修改映射规则")
        return

    print(f"[配置加载] 成功加载 {len(mapping)} 组标签修改规则。")
    media_dirs = [d.strip() for d in MEDIA_DIRS_ENV.split(",") if d.strip()]
    
    for m_dir in media_dirs:
        if not os.path.exists(m_dir):
            print(f"[跳过] 目录不存在: {m_dir}")
            continue
            
        print(f"[扫描] 正在扫描目录: {m_dir}")
        for root_dir, _, files in os.walk(m_dir):
            for file in files:
                if file.lower().endswith('.nfo'):
                    stats["total_nfo"] += 1
                    file_path = os.path.join(root_dir, file)
                    process_nfo(file_path, mapping)

    # 清理已生效的规则
    clean_modify_list_file()

    print(f"=== Emby NFO 标签精准修正结束 ===")
    send_summary_notification()

def send_summary_notification():
    """组装通知，优先合并发送，若超长则智能切片分批投递"""
    MAX_CHAR = 3500  # 预留安全字符线（Telegram 限制 4096）
    
    title = "Emby NFO 标签修正任务报告"
    summary_lines = [
        f"统计时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "✅ 运行模式：[精准原地修改已生效]",
        "----------------------------------",
        f"总扫描 NFO 文件数: {stats['total_nfo']} 个",
        f"已成功物理修改的 NFO 文件: {stats['modified_files_count']} 个",
        "----------------------------------",
        f"1. 修改演员名数量: {stats['actor_changes']} 处",
        f"2. 修改 Genre 数量: {stats['genre_changes']} 处",
        f"3. 修改 Tag 数量: {stats['tag_changes']} 处",
        "----------------------------------",
        f"💡 本次运行已自动擦除 modify.list 中已生效的 {len(triggered_rules)} 条规则。",
        "----------------------------------"
    ]
    summary_text = "\n".join(summary_lines)

    if not stats["affected_fanhao"]:
        send_notification(title, summary_text + "\n无受影响的番号。")
        return

    fh_list = sorted(list(stats["affected_fanhao"]))
    full_content = summary_text + "\n【受影响的关联番号如下】:\n" + "\n".join(fh_list)
    
    if len(full_content) <= MAX_CHAR:
        send_notification(title, full_content)
    else:
        chunk = []
        part = 1
        for fh in fh_list:
            chunk.append(fh)
            if len("\n".join(chunk)) > MAX_CHAR:
                send_notification(f"Emby修正:关联番号清单 Part {part}", "\n".join(chunk))
                chunk = []
                part += 1
        if chunk:
            send_notification(f"Emby修正:关联番号清单 Part {part}", "\n".join(chunk))
            
        send_notification(title, summary_text + f"\n💡 详细受影响的番号名单已在上方分作 {part} 条消息独立投递。")

def send_notification(title, content):
    """安全调用青龙内置的 sendNotify.py"""
    try:
        import sys
        sys.path.append('/ql/data/scripts')
        sys.path.append('/ql/scripts')
        from sendNotify import send
        send(title, content)
    except Exception as e:
        print(f"[通知失败] 无法加载或调用 sendNotify.py: {e}")

if __name__ == "__main__":
    main()
