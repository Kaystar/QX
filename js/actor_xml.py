# -*- coding: utf-8 -*-
# ql author: actor_mapping_sync
# ql name: 🎭 演员映射表同步与合并
# ql cron: 0 6 * * *
# ql desc: 从GitHub获取演员映射XML，与本地自定义修改合并，生成最终映射文件并发送通知
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

def send_via_ql_sendnotify(title, content):
    """调用青龙原生的 sendNotify.py 模块发送 Telegram 通知"""
    ql_paths = ['/ql/data/scripts', '/ql/scripts', './']
    for p in ql_paths:
        if p not in sys.path:
            sys.path.append(p)
    try:
        import sendNotify
        sendNotify.send(title, content)
        print("【通知】⚡ 成功投递美化版 Telegram 通知！")
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
        return 0, 0, 0

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
    return len(added), len(updated), len(deleted)

def merge_actor_xml_penta_lock_verbose(repo_file, my_file, output_path):
    """带明细日志记录的五重指纹层级覆盖熔断合并算法"""
    if not os.path.exists(repo_file) or not os.path.exists(my_file): return False, 0
    try:
        dir_name = os.path.dirname(output_path)
        if dir_name and not os.path.exists(dir_name): os.makedirs(dir_name, exist_ok=True)
        repo_tree = ET.parse(repo_file)
        repo_actor_node = repo_tree.getroot().find('actor')
        my_actor_node = ET.parse(my_file).getroot().find('actor')
        if repo_actor_node is None or my_actor_node is None: return False, 0

        repo_children, change_count, overwritten_logs = list(repo_actor_node), 0, []
        for my_child in my_actor_node:
            my_jp, my_kw = my_child.get('jp'), my_child.get('keyword')
            my_zh, my_tw = my_child.get('zh_cn'), my_child.get('zh_tw')
            my_bio_prefix = get_bio_prefix(my_child.get('bio_graphy'))
            if not any([my_jp, my_kw, my_zh, my_tw, my_bio_prefix]): continue
            change_count += 1
            matched_node, strategy = None, "未知"

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
                modded_fields = [f"{k}➔'{v}'" for k, v in my_child.attrib.items() if matched_node.get(k) != v]
                overwritten_logs.append(f"  ⚡ 拦截成功 ➔ {actor_name} [{strategy}] | 强写 ➔ {', '.join(modded_fields)}")
                matched_node.attrib.clear()
                for attr_name, attr_value in my_child.attrib.items(): matched_node.set(attr_name, attr_value)
            else:
                overwritten_logs.append(f"  ➕ 原创新增 ➔ {my_zh or my_jp} [全库查无此人，安全追加至末尾]")
                repo_actor_node.append(my_child)

        print("🛠️ ==================================================")
        print(f"🔧           🧩 本地 MY-CHANGES 自定义覆盖日志 ({change_count}条) ")
        print("====================================================")
        for log in overwritten_logs: print(log)
        print("====================================================\n")
        repo_tree.write(output_path, encoding='utf-8', xml_declaration=True)
        return True, change_count
    except Exception as e:
        print(f"【错误】XML 合并失败: {e}"); return False, 0

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
            added, updated, deleted = print_detailed_diff(LOCAL_REPO_FILE, temp_repo_path)
            os.replace(temp_repo_path, LOCAL_REPO_FILE)
            
            temp_final_path = FULL_OUTPUT_PATH + ".tmp_final"
            merge_success, my_count = merge_actor_xml_penta_lock_verbose(LOCAL_REPO_FILE, MY_CHANGES_FILE, temp_final_path)
            
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
                        
                        # 🛠️ 智能看板：如果没有增删改，优雅提示暂无变更；有变动则清晰罗列
                        if added == 0 and updated == 0 and deleted == 0:
                            diff_text = "暂无变更"
                        else:
                            diff_text = f"新增 {added} / 修改 {updated} / 移除 {deleted}"
                        
                        # ==================== ✨ 无符号至臻排版修正 ====================
                        content = (
                            f"📊 远程变动: {diff_text}\n"
                            f"⚙️ 规则覆盖: {my_count} 位演员\n"
                            f"📁 存储路径: {FULL_OUTPUT_PATH}\n"
                            f"⏰ 同步时间: {current_time}"
                        )
                        # ==============================================================
                        send_via_ql_sendnotify(title, content)
                    else:
                        print("【日志】由于本地原本无最终成品文件，本次为首次系统初始化运行，根据冷启动规则不发送 Telegram 通知。")
        else: print(f"【警告】请求 GitHub 失败，状态码: {response.status_code}")
    except Exception as e: print(f"【错误】网络连接失败: {e}")
