#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
cron: 0 3 * * *
new Env('🔍 NFO演员特殊字符检查');
"""

import os
import re
import xml.etree.ElementTree as ET
from datetime import datetime

# ================= 环境变量说明与获取 =================
# MEDIA_DIR: 必填，Emby媒体目录路径，多个目录用英文逗号(,)分隔。
# 示例：/media/movies,/media/tv
# ====================================================

MEDIA_DIRS_ENV = os.environ.get("MEDIA_DIR", "")

stats = {
    "total_nfo": 0,
    "match_deleted_actors": set(),  
    "match_modified_actors": set(), 
    "match_deleted_genres": 0,
    "match_modified_genres": 0,
    "match_deleted_tags": 0,
    "match_modified_tags": 0,
    "affected_files": 0,
    "corrupted_files": 0            
}

def is_japanese(char):
    return any([
        '\u3040' <= char <= '\u309F',
        '\u30A0' <= char <= '\u30FF',
        '\u4E00' <= char <= '\u9FFF'
    ])

def is_chinese(char):
    return '\u4E00' <= char <= '\u9FFF'

def process_actor_name(name):
    if not name:
        return 'skip', None
    name_strip = name.strip()
    if re.match(r'^[a-zA-Z\s]+$', name_strip):
        return 'skip', None
    if re.match(r'^[\d\W_]+$', name_strip):
        return 'delete', None

    has_alpha = bool(re.search(r'[a-zA-Z]', name_strip))
    has_digit_or_symbol = bool(re.search(r'[\d\W_]', name_strip))
    has_c_or_j = any(is_japanese(c) or is_chinese(c) for c in name_strip)

    if has_c_or_j and has_alpha and not has_digit_or_symbol:
        new_name = re.sub(r'[a-zA-Z]', '', name_strip).strip()
        return ('modify', new_name) if new_name else ('delete', None)
            
    if has_digit_or_symbol:
        new_name = "".join([c for c in name_strip if c.isalpha()]).strip()
        return ('modify', new_name) if new_name else ('delete', None)

    return 'skip', None

def clean_xml_string(xml_str):
    illegal_chars_re = re.compile(r'[\x00-\x08\x0B\x0C\x0E-\x1F\x7F-\x84\x86-\x9F]')
    xml_str = illegal_chars_re.sub('', xml_str)
    xml_str = re.sub(r'&(?!(amp|lt|gt|quot|apos|#\d+);)', '&amp;', xml_str)
    return xml_str

def check_nfo(file_path):
    try:
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            raw_content = f.read()
        cleaned_content = clean_xml_string(raw_content)
        root = ET.fromstring(cleaned_content)
    except Exception as e:
        print(f"[跳过破损文件] NFO 语法严重错误(如标签未闭合): {file_path}, 错误信息: {e}")
        stats["corrupted_files"] += 1
        return

    is_file_affected = False
    actors_to_delete = set()
    actors_to_modify = set()

    # 1. 分析演员节点
    for actor in root.findall('actor'):
        name_node = actor.find('name')
        if name_node is not None and name_node.text:
            orig_name = name_node.text
            action, new_name = process_actor_name(orig_name)
            
            if action == 'delete':
                actors_to_delete.add(orig_name)
                stats["match_deleted_actors"].add(orig_name)
                is_file_affected = True
                print(f"  [发现异常演员-待删] {orig_name}")
            elif action == 'modify':
                actors_to_modify.add(orig_name)
                stats["match_modified_actors"].add(orig_name)
                is_file_affected = True
                print(f"  [发现异常演员-待改] {orig_name}")

    # 2. 分析关联的 genre 节点
    for genre in root.findall('genre'):
        if genre.text in actors_to_delete:
            stats["match_deleted_genres"] += 1
            is_file_affected = True
        elif genre.text in actors_to_modify:
            stats["match_modified_genres"] += 1
            is_file_affected = True

    # 3. 分析关联的 tag 节点
    for tag in root.findall('tag'):
        if tag.text in actors_to_delete:
            stats["match_deleted_tags"] += 1
            is_file_affected = True
        elif tag.text in actors_to_modify:
            stats["match_modified_tags"] += 1
            is_file_affected = True

    if is_file_affected:
        stats["affected_files"] += 1

def main():
    print(f"=== Emby NFO 演员标签查询分析开始({os.path.basename(__file__)}) ===")
    if not MEDIA_DIRS_ENV:
        print("[警告] 未检测到环境变量 MEDIA_DIR。")
        send_notification("Emby NFO 查询失败", "未检测到环境变量 MEDIA_DIR")
        return

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
                    check_nfo(file_path)

    print(f"=== Emby NFO 演员标签查询分析结束 ===")
    send_summary_notification()

def send_summary_notification():
    """组装精简通知，只展示原名，分批发送"""
    MAX_CHAR = 3000 
    
    # --- 阶段 1：发送命中删除规则的演员原名 ---
    if stats["match_deleted_actors"]:
        del_list = sorted(list(stats["match_deleted_actors"]))
        chunk = []
        part = 1
        for actor in del_list:
            chunk.append(actor)
            if len("\n".join(chunk)) > MAX_CHAR:
                send_notification(f"Emby查询:异常演员清单A Part {part}", "\n".join(chunk))
                chunk = []
                part += 1
        if chunk:
            send_notification(f"Emby查询:异常演员清单A Part {part}" if part > 1 else "Emby查询:异常演员清单A(待删类)", "\n".join(chunk))

    # --- 阶段 2：发送命中修改规则的演员原名 ---
    if stats["match_modified_actors"]:
        mod_list = sorted(list(stats["match_modified_actors"]))
        chunk = []
        part = 1
        for actor in mod_list:
            chunk.append(actor)
            if len("\n".join(chunk)) > MAX_CHAR:
                send_notification(f"Emby查询:异常演员清单B Part {part}", "\n".join(chunk))
                chunk = []
                part += 1
        if chunk:
            send_notification(f"Emby查询:异常演员清单B Part {part}" if part > 1 else "Emby查询:异常演员清单B(待改类)", "\n".join(chunk))

    # --- 阶段 3：发送最终总计报告 ---
    title = "🔍 NFO演员特殊字符查询完成"
    summary_lines = [
        f"统计时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "⚠️ 当前运行模式：[仅查询分析，未修改任何物理文件]",
        "----------------------------------",
        f"总扫描 NFO 文件数: {stats['total_nfo']} 个",
        f"存在异常标签的 NFO 文件: {stats['affected_files']} 个 (关联番号数)",
        f"无法解析的破损 NFO 文件: {stats['corrupted_files']} 个 (已自动跳过)",
        "----------------------------------",
        f"1. 匹配待删演员种类: {len(stats['match_deleted_actors'])} 个",
        f"2. 匹配待改演员种类: {len(stats['match_modified_actors'])} 个",
        f"3. 命中待删 Genre 数量: {stats['match_deleted_genres']} 处",
        f"4. 命中待改 Genre 数量: {stats['match_modified_genres']} 处",
        f"5. 命中待删 Tag 数量: {stats['match_deleted_tags']} 处",
        f"6. 命中待改 Tag 数量: {stats['match_modified_tags']} 处",
        "----------------------------------",
        "💡 详细名单已在上方消息中完整投递。"
    ]
    send_notification(title, "\n".join(summary_lines))

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
