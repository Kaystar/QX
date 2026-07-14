#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
================================================================================
脚本名称: Emby NFO 标签/演员精准删除工具 (容错优化版)
脚本用途: 
  主要用于自动化批量删除 Emby/Jellyfin 媒体库 NFO 文件中的特定演员、流派(genre)和标签(tag)。
  当你在删除名单中指定一个名称时，脚本会启动“严格模式”在 NFO 中检索，一旦命中，
  将同时从演员列表、流派列表、标签列表中将其彻底抹除。

核心特性:
  1. 智能空格容错：自动将全角空格、连续多空格统一化处理，防止因“空格类型不同”导致无法匹配。
  2. 完美支持数字前缀：如“1橘 医師”，只要名单与 NFO 字符一致即可精准命中。
  3. 完美排版不留痕：重构了 XML 缩进逻辑，删除节点后自动格式化，绝不留空行，
     也绝不造成多个标签挤在一行的情况。
  4. 任务清单自动清理：物理删除成功后，会自动从本地 `delete.list` 清单中剔除已生效
     的规则，未触发的规则继续保留。
  5. 智能通知合并：优先将统计报告与番号清单合并为一条 Telegram 通知发送，只有当字数
     超过电报限制（安全线 3500 字）时，才会自动切片分批投递。

使用说明:
  1. 环境变量配置 (青龙面板/Docker)：
     - MEDIA_DIR: 必填。Emby 媒体目录，多个目录用英文逗号隔开。
                  示例：/media/movies,/media/tv
     - DELETE_LIST: 选填。若不想用文件，可直接在此环境变量中输入删除名单（一行一个）。
  2. 本地文件配置 (推荐)：
     - 在脚本同级目录下创建一个名为 `delete.list` 的文件。
     - 写入你想要删除的演员/标签名称（支持带数字前缀，如 `1橘 医師`），每行一个。
  3. 运行后：
     - 脚本会自动扫描 NFO 并进行原地物理擦除与排版优化。

cron: 0 5 * * *
new Env('NFO标签精准删除工具');
================================================================================
"""

import os
import re
import xml.etree.ElementTree as ET
from datetime import datetime

# 获取环境变量
MEDIA_DIRS_ENV = os.environ.get("MEDIA_DIR", "")
DELETE_LIST_ENV = os.environ.get("DELETE_LIST", "")

# 全局统计数据
stats = {
    "total_nfo": 0,
    "modified_files_count": 0,
    "actor_deletions": 0,
    "genre_deletions": 0,
    "tag_deletions": 0,
    "affected_fanhao": set()  # 记录关联的番号/文件名
}

# 记录哪些删除规则在此次运行中真正产生了修改（存储标准化后的名字）
triggered_rules = set()
# 映射表：标准化名字 -> 原始输入名字（用于回写并清理 delete.list）
norm_to_raw_mapping = {}

def normalize_name(name):
    """
    标准化名字函数：
    1. 去除首尾空白
    2. 将全角空格、制表符等所有空白字符统一替换为单张半角空格
    """
    if not name:
        return ""
    # 替换所有空白字符（含全角空格 \u3000）为半角空格
    standardized = re.sub(r'[\s\u3000]+', ' ', name)
    return standardized.strip()

def parse_delete_list():
    """解析删除名单（优先读同级文件，次之读环境变量）"""
    delete_set = set()
    current_dir = os.path.dirname(os.path.abspath(__file__))
    file_path = os.path.join(current_dir, "delete.list")
    
    content = ""
    if os.path.exists(file_path):
        print(f"[配置加载] 检测到本地删除清单: {file_path}，正在读取...")
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                content = f.read()
        except Exception as e:
            print(f"[错误] 读取本地 delete.list 失败: {e}")
    else:
        print(f"[配置加载] 未检测到本地 delete.list 文件，尝试读取环境变量 DELETE_LIST...")
        content = DELETE_LIST_ENV

    if not content:
        return delete_set

    lines = content.strip().split('\n')
    for line in lines:
        raw_item = line.strip()
        if raw_item and not raw_item.startswith("#"): # 支持井号注释
            norm_item = normalize_name(raw_item)
            if norm_item:
                delete_set.add(norm_item)
                norm_to_raw_mapping[norm_item] = raw_item # 记住原始写法以便后续精准删除
    return delete_set

def clean_delete_list_file():
    """从本地 delete.list 文件中清理掉已经生效过的删除规则"""
    current_dir = os.path.dirname(os.path.abspath(__file__))
    file_path = os.path.join(current_dir, "delete.list")
    
    if not os.path.exists(file_path):
        return
        
    if not triggered_rules:
        print("[列表清理] 没有规则被触发删除，无需清理本地文件。")
        return

    # 找出需要被删掉的原始行内容
    raw_triggered_items = {norm_to_raw_mapping[norm] for norm in triggered_rules if norm in norm_to_raw_mapping}

    print(f"[列表清理] 正在清理已生效的删除规则...")
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
            
        new_lines = []
        removed_count = 0
        for line in lines:
            item = line.strip()
            if item in raw_triggered_items:
                removed_count += 1
                continue  # 跳过这一行，即删除
            new_lines.append(line)
            
        with open(file_path, "w", encoding="utf-8") as f:
            f.writelines(new_lines)
            
        print(f"[列表清理] 清理完成，已从 delete.list 中移除 {removed_count} 条已生效规则。")
    except Exception as e:
        print(f"[错误] 清理本地 delete.list 文件失败: {e}")

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

def reset_and_indent(elem, level=0):
    """
    规整排版核心函数：
    在删除节点后，完全清除原有的残留空白符，并基于当前树结构重新计算标准缩进。
    """
    i = "\n" + level * "  "
    if len(elem):
        if not elem.text or not elem.text.strip():
            elem.text = i + "  "
        if not elem.tail or not elem.tail.strip():
            elem.tail = i
        for child in elem:
            reset_and_indent(child, level + 1)
        if not child.tail or not child.tail.strip():
            child.tail = i
    else:
        elem.text = elem.text.strip() if elem.text else ""
        if level and (not elem.tail or not elem.tail.strip()):
            elem.tail = i

def process_nfo(file_path, delete_set):
    """核心删除逻辑"""
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
    
    # 局部临时计数器
    local_actor_dels = 0
    local_genre_dels = 0
    local_tag_dels = 0

    # 1. 严格审查并删除演员节点
    for actor in root.findall('actor')[:]:
        name_node = actor.find('name')
        if name_node is not None and name_node.text:
            target_name = normalize_name(name_node.text)
            if target_name in delete_set:
                root.remove(actor)
                local_actor_dels += 1
                triggered_rules.add(target_name)
                is_file_changed = True
                print(f"  [物理擦除演员] 命中: {target_name} ({base_name})")

    # 2. 严格审查并删除 genre 节点
    for genre in root.findall('genre')[:]:
        if genre.text:
            target_genre = normalize_name(genre.text)
            if target_genre in delete_set:
                root.remove(genre)
                local_genre_dels += 1
                triggered_rules.add(target_genre)
                is_file_changed = True
                print(f"  [物理擦除Genre] 命中: {target_genre} ({base_name})")

    # 3. 严格审查并删除 tag 节点
    for tag in root.findall('tag')[:]:
        if tag.text:
            target_tag = normalize_name(tag.text)
            if target_tag in delete_set:
                root.remove(tag)
                local_tag_dels += 1
                triggered_rules.add(target_tag)
                is_file_changed = True
                print(f"  [物理擦除Tag] 命中: {target_tag} ({base_name})")

    # 如果发生了节点移除，重新排版并物理回写文件
    if is_file_changed:
        # 彻底清空旧尾巴，重新灌入标准双空格缩进
        for node in root.iter():
            node.tail = None
            if len(node) == 0:
                node.text = node.text.strip() if node.text else ""
        reset_and_indent(root)
        
        tree = ET.ElementTree(root)
        try:
            tmp_path = file_path + ".tmp"
            tree.write(tmp_path, encoding='utf-8', xml_declaration=True)
            if os.path.exists(file_path):
                os.remove(file_path)
            os.rename(tmp_path, file_path)
            
            # 物理写入成功，计入全局统计
            stats["modified_files_count"] += 1
            stats["affected_fanhao"].add(base_name)
            stats["actor_deletions"] += local_actor_dels
            stats["genre_deletions"] += local_genre_dels
            stats["tag_deletions"] += local_tag_dels
            
        except Exception as e:
            print(f"[物理写入失败] 文件: {file_path}, 错误: {e}")

def main():
    print(f"=== Emby NFO 标签精准删除开始 ===")
    
    if not MEDIA_DIRS_ENV:
        print("[错误] 未检测到环境变量 MEDIA_DIR。")
        send_notification("Emby标签删除失败", "未检测到环境变量 MEDIA_DIR")
        return

    delete_set = parse_delete_list()
    if not delete_set:
        print("[提示] 未找到有效的删除规则，请检查本地 delete.list 文件或环境变量 DELETE_LIST。")
        return

    print(f"[配置加载] 成功加载 {len(delete_set)} 条精准删除规则。")
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
                    process_nfo(file_path, delete_set)

    # 清理已生效的删除规则
    clean_delete_list_file()

    print(f"=== Emby NFO 标签精准删除结束 ===")
    send_summary_notification()

def send_summary_notification():
    """组装通知，优先合并发送，若超长则智能切片分批投递"""
    MAX_CHAR = 3500
    
    title = "Emby NFO 标签擦除任务报告"
    summary_lines = [
        f"统计时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "🗑️ 运行模式：[精准原地擦除已生效]",
        "----------------------------------",
        f"总扫描 NFO 文件数: {stats['total_nfo']} 个",
        f"已成功物理瘦身的 NFO 文件: {stats['modified_files_count']} 个",
        "----------------------------------",
        f"1. 擦除演员名数量: {stats['actor_deletions']} 处",
        f"2. 擦除 Genre 数量: {stats['genre_deletions']} 处",
        f"3. 擦除 Tag 数量: {stats['tag_deletions']} 处",
        "----------------------------------",
        f"💡 本次运行已自动擦除 delete.list 中已生效的 {len(triggered_rules)} 条规则。",
        "----------------------------------"
    ]
    summary_text = "\n".join(summary_lines)

    if not stats["affected_fanhao"]:
        send_notification(title, summary_text + "\n没有检测到匹配的冗余标签，未做任何修改。")
        return

    fh_list = sorted(list(stats["affected_fanhao"]))
    full_content = summary_text + "\n【已完成标签瘦身的番号如下】:\n" + "\n".join(fh_list)
    
    if len(full_content) <= MAX_CHAR:
        send_notification(title, full_content)
    else:
        chunk = []
        part = 1
        for fh in fh_list:
            chunk.append(fh)
            if len("\n".join(chunk)) > MAX_CHAR:
                send_notification(f"Emby删除:受影响番号清单 Part {part}", "\n".join(chunk))
                chunk = []
                part += 1
        if chunk:
            send_notification(f"Emby删除:受影响番号清单 Part {part}", "\n".join(chunk))
            
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
