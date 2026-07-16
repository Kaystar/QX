# -*- coding: utf-8 -*-
# ql author: actor_mapping_sync
# ql name: 🎭 演员映射表同步与合并
# ql cron: 0 6 * * *
# ql desc: 从GitHub获取演员映射XML，与本地自定义修改(支持分流覆盖与删除)合并，生成最终映射文件并发送通知
# ==================== 🛠️ 青龙环境变量配置指南 ====================
# 1. ACTOR_REPO_URL    : GitHub远程演员映射XML文件的URL。  [默认值: https://githubusercontent.com]
# 2. ACTOR_LOCAL_FILE  : 本地存储的GitHub下载文件名称。    [默认值: github-downloaded.xml]
# 3. ACTOR_MY_CHANGES  : 本地自定义修改的XML文件名称。    [默认值: my-changes.xml]
# 4. ACTOR_FINAL_OUTPUT: 最终合并输出的文件名称。          [默认值: final-actor-mapping.xml]
# 5. ACTOR_OUTPUT_DIR  : 输出文件的目录路径。              [默认值: 空(当前目录)]
# =================================================================

import os
import xml.etree.ElementTree as ET
import requests
import hashlib
import sys
import datetime

# ==================== 动态读取青龙环境变量 ====================
REPO_URL = os.environ.get('ACTOR_REPO_URL', 'https://githubusercontent.com')
LOCAL_REPO_FILE = os.environ.get('ACTOR_LOCAL_FILE', 'github-downloaded.xml')
MY_CHANGES_FILE = os.environ.get('ACTOR_MY_CHANGES', 'my-changes.xml')
FINAL_OUTPUT_FILE = os.environ.get('ACTOR_FINAL_OUTPUT', 'final-actor-mapping.xml')
OUTPUT_DIR = os.environ.get('ACTOR_OUTPUT_DIR', '').strip()
# ==============================================================

def split_message(text, max_length=3500):
    """安全地将长文本按行切分成多段，避免超过 Telegram 单条长度限制"""
    if len(text) <= max_length:
        return [text]
    
    chunks = []
    current_chunk = []
    current_length = 0
    
    for line in text.split('\n'):
        if len(line) > max_length:
            if current_chunk:
                chunks.append('\n'.join(current_chunk))
                current_chunk = []
                current_length = 0
            for i in range(0, len(line), max_length):
                chunks.append(line[i:i+max_length])
            continue
            
        line_len = len(line) + 1
        if current_length + line_len > max_length:
            chunks.append('\n'.join(current_chunk))
            current_chunk = [line]
            current_length = line_len
        else:
            current_chunk.append(line)
            current_length += line_len
            
    if current_chunk:
        chunks.append('\n'.join(current_chunk))
        
    return chunks

def send_via_ql_sendnotify(title, content):
    """调用青龙原生的 sendNotify.py 模块发送 Telegram 通知"""
    ql_paths = ['/ql/data/scripts', '/ql/scripts', './']
    for p in ql_paths:
        if p not in sys.path:
            sys.path.append(p)
    try:
        import sendNotify
        message_chunks = split_message(content, max_length=3500)
        total_pages = len(message_chunks)
        
        for idx, chunk in enumerate(message_chunks):
            page_title = f"{title} ({idx + 1}/{total_pages})" if total_pages > 1 else title
            sendNotify.send(page_title, chunk)
            print(f"【通知】⚡ 成功投递分包通知 [{idx + 1}/{total_pages}]")
            
    except Exception as e:
        print(f"【错误】调用 sendNotify 失败: {e}")

def get_file_md5(file_path):
    if not os.path.exists(file_path): return ""
    hash_md5 = hashlib.md5()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(4096), b""): hash_md5.update(chunk)
    return hash_md5.hexdigest()

def get_bio_prefix(bio):
    return bio.strip()[:12] if bio else ""

def parse_xml_to_list(file_path):
    if not os.path.exists(file_path): return []
    try:
        root = ET.parse(file_path).getroot()
        actor_node = root.find('actor')
        if actor_node is not None: return [dict(child.attrib) for child in actor_node]
    except Exception: pass
    return []

def print_detailed_diff(old_file, new_file):
    old_list, new_list = parse_xml_to_list(old_file), parse_xml_to_list(new_file)
    if not old_list:
        print("【数据透视】本地无历史缓存，本次为全量首次导入，跳过明细对比。")
        return [], [], []

    def get_best_name(attr): return attr.get('zh_cn') or attr.get('jp') or attr.get('keyword') or "未知演员"
    old_dict = {attr.get('jp'): attr for attr in old_list if attr.get('jp')}
    new_dict = {attr.get('jp'): attr for attr in new_list if attr.get('jp')}
    added, updated, deleted = [], [], []

    for jp, new_attr in new_dict.items():
        name = get_best_name(new_attr)
        if jp not in old_dict:
            kw_match = False
            for o_jp, o_attr in old_dict.items():
                if o_attr.get('keyword') == new_attr.get('keyword') or o_attr.get('zh_cn') == new_attr.get('zh_cn'):
                    kw_match = True
                    if o_attr != new_attr:
                        diffs = [f"{k}: '{o_attr.get(k)}' ➔ '{v}'" for k, v in new_attr.items() if o_attr.get(k) != v]
                        updated.append((name, diffs))
                    break
            if not kw_match: added.append(name)
        elif old_dict[jp] != new_attr:
            diffs = [f"{k}: '{old_dict[jp].get(k)}' ➔ '{v}'" for k, v in new_attr.items() if old_dict[jp].get(k) != v]
            updated.append((name, diffs))

    for jp, old_attr in old_dict.items():
        name = get_best_name(old_attr)
        if jp not in new_dict and not any(n.get('keyword') == old_attr.get('keyword') or n.get('zh_cn') == old_attr.get('zh_cn') for n in new_dict.values()):
            deleted.append(name)

    print("\n📊 ==================================================")
    print("📋            💾 GITHUB 远程数据变动深度透视           ")
    print("====================================================")
    if added:
        for item in added: print(f"  └── [新增] ➔ {item}")
    if updated:
        for name, diffs in updated:
            print(f"  ├── [修改] ➔ {name}")
            for d in diffs: print(f"  │    └── ⚙️ {d}")
    if deleted:
        for item in deleted: print(f"  └── [移除] ➔ {item}")
    if not added and not updated and not deleted:
        print("✨ 【检测结果】远程仓库处于静态，本次并无任何演员增删改动。")
    print("====================================================\n")
    return added, updated, deleted

def merge_actor_xml_penta_lock_verbose(repo_file, my_file, output_path):
    """带明细日志记录的【结构分离版】覆盖与整行物理删除合并算法"""
    if not os.path.exists(repo_file) or not os.path.exists(my_file): return False, {}
    try:
        dir_name = os.path.dirname(output_path)
        if dir_name and not os.path.exists(dir_name): os.makedirs(dir_name, exist_ok=True)
        
        repo_tree = ET.parse(repo_file)
        repo_actor_node = repo_tree.getroot().find('actor')
        if repo_actor_node is None: return False, {}
        repo_children = list(repo_actor_node)

        my_root = ET.parse(my_file).getroot()
        my_update_node = my_root.find('update')
        my_delete_node = my_root.find('delete')

        overwritten_logs = []
        
        # 🔔 分类存储本地变更细节，用于结构化通知
        local_overwrites = []  # 属性覆盖修改
        local_additions = []   # 本地追加新增
        local_deletions = []   # 本地物理删除
        
        # ==================== 1. 执行物理删除逻辑 ====================
        if my_delete_node is not None:
            for my_child in my_delete_node:
                my_jp, my_kw = my_child.get('jp'), my_child.get('keyword')
                my_zh, my_tw = my_child.get('zh_cn'), my_child.get('zh_tw')
                my_bio_prefix = get_bio_prefix(my_child.get('bio_graphy'))
                
                if not any([my_jp, my_kw, my_zh, my_tw, my_bio_prefix]): continue
                matched_node, strategy = None, "未知"

                # 五重指纹定位
                if my_jp:
                    for node in repo_children:
                        if node.get('jp') == my_jp: matched_node, strategy = node, "日本名完全契合"; break
                if not matched_node and my_kw:
                    for node in repo_children:
                        if node.get('keyword') == my_kw: matched_node, strategy = node, "旧关键字雷同"; break
                if not matched_node and my_zh:
                    for node in repo_children:
                        if node.get('zh_cn') == my_zh: matched_node, strategy = node, "简体中文完全契合"; break
                if not matched_node and my_tw:
                    for node in repo_children:
                        if node.get('zh_tw') == my_tw: matched_node, strategy = node, "繁简双向互解"; break
                if not matched_node and my_bio_prefix:
                    for node in repo_children:
                        if get_bio_prefix(node.get('bio_graphy')) == my_bio_prefix: matched_node, strategy = node, "简介指纹物理召回"; break

                if matched_node is not None:
                    actor_name = matched_node.get('zh_cn') or matched_node.get('jp') or "未知"
                    
                    # 🔴 彻底从父节点与临时缓存中物理移除
                    repo_actor_node.remove(matched_node)
                    repo_children.remove(matched_node)
                    
                    overwritten_logs.append(f"  ❌ 物理删除 ➔ {actor_name} [{strategy}]")
                    local_deletions.append(actor_name)
                else:
                    actor_name = my_zh or my_jp or "未知"
                    overwritten_logs.append(f"  ⚠️ 忽略删除 ➔ {actor_name} [全库未查到该演员，无需执行删除]")

        # ==================== 2. 执行更新与覆盖逻辑 ====================
        if my_update_node is not None:
            for my_child in my_update_node:
                my_jp, my_kw = my_child.get('jp'), my_child.get('keyword')
                my_zh, my_tw = my_child.get('zh_cn'), my_child.get('zh_tw')
                my_bio_prefix = get_bio_prefix(my_child.get('bio_graphy'))
                
                if not any([my_jp, my_kw, my_zh, my_tw, my_bio_prefix]): continue
                matched_node, strategy = None, "未知"

                # 五重指纹定位
                if my_jp:
                    for node in repo_children:
                        if node.get('jp') == my_jp: matched_node, strategy = node, "日本名完全契合"; break
                if not matched_node and my_kw:
                    for node in repo_children:
                        if node.get('keyword') == my_kw: matched_node, strategy = node, "旧关键字雷同"; break
                if not matched_node and my_zh:
                    for node in repo_children:
                        if node.get('zh_cn') == my_zh: matched_node, strategy = node, "简体中文完全契合"; break
                if not matched_node and my_tw:
                    for node in repo_children:
                        if node.get('zh_tw') == my_tw: matched_node, strategy = node, "繁简双向互解"; break
                if not matched_node and my_bio_prefix:
                    for node in repo_children:
                        if get_bio_prefix(node.get('bio_graphy')) == my_bio_prefix: matched_node, strategy = node, "简介指纹物理召回"; break

                if matched_node is not None:
                    actor_name = matched_node.get('zh_cn') or matched_node.get('jp') or "未知"
                    
                    # 🔍 【重点过滤与精简包装】
                    # 分析修改了哪些字段
                    core_fields = ['zh_cn', 'zh_tw', 'jp']
                    changed_core_details = []
                    changed_other_keys = []
                    
                    for k, v in my_child.attrib.items():
                        old_v = matched_node.get(k)
                        if old_v != v:
                            if k in core_fields:
                                changed_core_details.append(f"{k}: '{old_v}' ➔ '{v}'")
                            else:
                                changed_other_keys.append(k)
                                
                    # 组装格式化展示逻辑
                    final_notice_parts = []
                    if changed_core_details:
                        final_notice_parts.extend(changed_core_details)
                    if changed_other_keys:
                        final_notice_parts.append(f"其他修改: {', '.join(changed_other_keys)}")
                    
                    # 控制台仍然记录完整变动
                    all_fields = [f"{k}: '{matched_node.get(k)}' ➔ '{v}'" for k, v in my_child.attrib.items() if matched_node.get(k) != v]
                    overwritten_logs.append(f"  ⚡ 覆盖拦截 ➔ {actor_name} [{strategy}] | 强写 ➔ {', '.join(all_fields)}")
                    
                    # 只有在确有属性变更时，才推入通知队列
                    if final_notice_parts:
                        local_overwrites.append((actor_name, final_notice_parts))
                    
                    matched_node.attrib.clear()
                    for attr_name, attr_value in my_child.attrib.items(): 
                        matched_node.set(attr_name, attr_value)
                else:
                    actor_name = my_zh or my_jp or "未知"
                    overwritten_logs.append(f"  ➕ 原创新增 ➔ {actor_name} [全库查无此人，安全追加至末尾]")
                    local_additions.append(actor_name)
                    repo_actor_node.append(my_child)

        # ==================== 3. 输出合并控制台日志 ====================
        total_rules = len(local_overwrites) + len(local_additions) + len(local_deletions)
        print("🛠️ ==================================================")
        print(f"🔧       🧩 本地 MY-CHANGES 自定义覆盖与删除日志 (共处理 {total_rules} 条有效规则)")
        print("====================================================")
        for log in overwritten_logs: print(log)
        print("====================================================\n")
        
        repo_tree.write(output_path, encoding='utf-8', xml_declaration=True)
        
        change_summary = {
            'overwrites': local_overwrites,
            'additions': local_additions,
            'deletions': local_deletions
        }
        return True, change_summary
    except Exception as e:
        print(f"【错误】XML 结构分离合并失败: {e}"); return False, {}

if __name__ == "__main__":
    FULL_OUTPUT_PATH = os.path.join(OUTPUT_DIR, FINAL_OUTPUT_FILE) if OUTPUT_DIR else FINAL_OUTPUT_FILE
    print("====== 正在读取青龙配置 ======\n远程链接:", REPO_URL, "\n输出目标:", FULL_OUTPUT_PATH, "\n==============================")
    current_final_md5 = get_file_md5(FULL_OUTPUT_PATH)
    current_time = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    print("正在从 GitHub 获取最新文件...")
    try:
        response = requests.get(REPO_URL, timeout=15)
        if response.status_code == 200:
            temp_repo_path = LOCAL_REPO_FILE + ".tmp_repo"
            with open(temp_repo_path, 'wb') as f: f.write(response.content)
            
            # 1. 获取远程变动名单与修改细则
            added_list, updated_list, deleted_list = print_detailed_diff(LOCAL_REPO_FILE, temp_repo_path)
            added, updated, deleted = len(added_list), len(updated_list), len(deleted_list)
            
            os.replace(temp_repo_path, LOCAL_REPO_FILE)
            
            temp_final_path = FULL_OUTPUT_PATH + ".tmp_final"
            # 2. 调用合并算法并捕获分类变更字典
            merge_success, local_changes = merge_actor_xml_penta_lock_verbose(LOCAL_REPO_FILE, MY_CHANGES_FILE, temp_final_path)
            
            if merge_success:
                new_final_md5 = get_file_md5(temp_final_path)
                if current_final_md5 and current_final_md5 == new_final_md5:
                    print("【提示】经最终数据死锁比对：成品内容完全一致，无实际变动。跳过通知与覆盖。")
                    os.remove(temp_final_path)
                else:
                    if os.path.exists(FULL_OUTPUT_PATH): os.remove(FULL_OUTPUT_PATH)
                    os.replace(temp_final_path, FULL_OUTPUT_PATH)
                    print(f"【成功】检测到内容发生实质性改变！新文件已保存至: {FULL_OUTPUT_PATH}")
                    
                    if current_final_md5:
                        title = "🎭 演员映射表更新成功"
                        total_actors = len(parse_xml_to_list(FULL_OUTPUT_PATH))
                        
                        # 提取本地变更细节
                        local_ow = local_changes.get('overwrites', [])
                        local_add = local_changes.get('additions', [])
                        local_del = local_changes.get('deletions', [])
                        
                        has_local_changes = local_ow or local_add or local_del
                        
                        # 3. 没有任何变动的情况
                        if added == 0 and updated == 0 and deleted == 0 and not has_local_changes:
                            diff_text = "暂无变更"
                            details_text = ""
                        else:
                            diff_text = f"新增 {added} / 修改 {updated} / 移除 {deleted}"
                            detail_lines = []
                            
                            # 📌 4. 远程变动展示
                            if added_list:
                                detail_lines.append("\n ➕ 【远程新增】")
                                detail_lines.extend([f"   • {name}" for name in added_list[:15]])
                                if added > 15: detail_lines.append(f"   • ...等共 {added} 人")
                            
                            if updated_list:
                                detail_lines.append("\n ⚙️ 【远程修改】")
                                for name, diffs in updated_list[:15]:
                                    diff_str = ", ".join(diffs)
                                    detail_lines.append(f"   • {name} ➔ ({diff_str})")
                                if updated > 15: detail_lines.append(f"   • ...等共 {updated} 人")
                                
                            if deleted_list:
                                detail_lines.append("\n ❌ 【远程移除】")
                                detail_lines.extend([f"   • {name}" for name in deleted_list[:15]])
                                if deleted > 15: detail_lines.append(f"   • ...等共 {deleted} 人")
                                
                            # 📌 5. 本地覆盖/新增展示
                            if local_ow or local_add:
                                detail_lines.append("\n 🧩 【本地覆写】")
                                # 展示修改 (此时已根据您的要求精准包装)
                                for name, diffs in local_ow[:15]:
                                    diff_str = ", ".join(diffs)
                                    detail_lines.append(f"   • {name} ➔ ({diff_str})")
                                # 后展示新增
                                remaining_slots = 15 - len(local_ow)
                                if remaining_slots > 0 and local_add:
                                    for name in local_add[:remaining_slots]:
                                        detail_lines.append(f"   • {name} ➔ (原创新增，安全追加)")
                                
                                total_ow_add = len(local_ow) + len(local_add)
                                if total_ow_add > 15:
                                    detail_lines.append(f"   • ...等共 {total_ow_add} 人")
                                    
                            # 📌 6. 本地物理删除展示
                            if local_del:
                                detail_lines.append("\n ❌ 【本地删除】")
                                detail_lines.extend([f"   • {name} ➔ (已物理抹除)" for name in local_del[:15]])
                                if len(local_del) > 15:
                                    detail_lines.append(f"   • ...等共 {len(local_del)} 人")
                            
                            details_text = "\n" + "\n".join(detail_lines)
                        
                        # ⚙️ 本地覆写状态行进行精准数量分流统计
                        local_status_str = f"{len(local_ow)} 位更新 / {len(local_add)} 位新增 / {len(local_del)} 位删除"
                        
                        # 无符号至臻排版修正
                        content = (
                            f"📊 远程变动: {diff_text}\n"
                            f"👥 演员总数: {total_actors} 位演员\n"
                            f"⚙️ 本地覆写: {local_status_str}\n"
                            f"📁 存储路径: {FULL_OUTPUT_PATH}\n"
                            f"⏰ 同步时间: {current_time}"
                            f"{details_text}"
                        )
                        send_via_ql_sendnotify(title, content)
                    else:
                        print("【日志】由于本地原本无最终成品文件，本次为首次 system 初始化运行，根据冷启动规则不发送 Telegram 通知。")
        else: print(f"【警告】请求 GitHub 失败，状态码: {response.status_code}")
    except Exception as e: print(f"【错误】网络连接失败: {e}")
