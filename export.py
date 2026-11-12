"""
微信群文字打卡导出与统计模块。

主要功能：
    - 自动提取微信群的文本、图片、文件等消息
    - 自动解析“备注 + 图片/文件”形成完整打卡记录
    - 支持自动推断学生学号（昵称未含学号时从文本中识别）
    - 支持自定义三类打卡（英语：单词词组 / 课文背诵 / 语法知识点）
    - 支持数学、语文不同群的统计逻辑
    - 自动生成 txt 和 csv 文件
    - 提供 GroupFactory 根据群名称自动选择对应处理逻辑

模块结构概览：
    - Group（基类）
    - EnglishGroup（处理英语打卡）
    - MathsGroup（处理数学/语文打卡）
    - GroupFactory（根据群名创建对应的 Group 实例）
    - 各类辅助解析方法（提取学号、文本分类、状态机处理）

该模块既可被主程序调用，也可以独立运行（CLI）。
"""
import argparse
import sys


def _build_parser():
    """构建命令行参数解析器（仅依赖 argparse，供 --help 提前退出复用）。"""
    return argparse.ArgumentParser(
        description="导出微信群文字并统计打卡。",
    )


# 提前处理 --help / -h：只打印帮助即退出，避免加载重模块
if "--help" in sys.argv or "-h" in sys.argv:
    _build_parser().print_help()
    sys.exit(0)


from wechat_compat import WeChat
import os
from datetime import datetime, timedelta
import re
from enum import Enum
import random
from wxname2studentid import Wxname2StudentID
from checkinstats import CheckInStats
from logger import get_logger, debug_dump_raw_messages, debug_dump_text_msgs
from safewechat import safe_get_wechat_window, safe_bring_wechat_to_front, safe_scroll, safe_scroll_and_collect, safe_open_chat, safe_get_all_messages, safe_send_msg_immediately, move_mouse_to_chat_center, hide_tkinter_root, SCROLL_PIXEL, set_wechat_window_size
from message import is_text_message, is_pic_message, is_time_message, is_file_message, is_memo_message, is_video_message, is_audio_message, compare_message_date, get_sender_name, is_self_or_bot_message
from config import get_env, get_env_set, ask_yes_no, find_excel_file
from version import check_version_or_exit

group_names = get_env_set("CHECKIN_GROUP_NAMES")
checkin_dir = get_env("CHECKIN_DIR")

def create_number_set():
    """
    创建一个学号集合（29101–29118 与 29151–29182）。

    :return: 学号集合
    :rtype: set[int]
    """    
    s1 = set(range(29101, 29119))   # 29101–29118
    s2 = set(range(29151, 29183))   # 29151–29182
    return s1.union(s2)

def remove_found_numbers(student_id, num_set):
    """
    从学号集合中移除已完成打卡的学生。

    :param student_id: 已处理的学号
    :type student_id: int
    :param num_set: 当前学号集合
    :type num_set: set[int]
    :return: 处理后的集合
    :rtype: set[int]
    """    
    num_set.discard(student_id)    # 从集合中删除找到的数字
    return num_set

def get_student_id_from_string(input_string):
    """
    从任意文本中提取学号，并返回学号与去除学号后的剩余文本。

    逻辑：
        - 搜索所有数字序列
        - 只保留在合法学号集合内的数字
        - 若出现多个学号 → 取在字符串中出现最靠前的
        - 返回：
            (学号, “除去学号后的打卡内容”)
        - 若无合法学号 → 返回 ("", "")

    :param input_string: 输入文本内容
    :type input_string: str
    :return: (student_id, remainder_text)
    :rtype: tuple[str, str]
    """    
    nums = create_number_set()

    # 找出字符串中的所有数字
    all_numbers = re.findall(r"\d+", input_string)
    
    # 筛选出在 nums 集合中的学号
    valid_ids = [num for num in all_numbers if int(num) in nums]

    # 如果没有合法学号，返回0和空字符串
    if not valid_ids:
        return "", ""

    # 如果有多个合法学号，取第一个在原字符串中出现的位置最靠前的
    first_valid_id = min(valid_ids, key=lambda x: input_string.find(x))

    # 找到学号在原字符串中的位置
    idx = input_string.find(first_valid_id)

    # 提取学号前后的文本
    before = input_string[:idx].strip()
    after = input_string[idx + len(first_valid_id):].strip()

    # 打卡内容为除去学号的剩余部分
    check_in = (before + " " + after).strip()

    return first_valid_id, check_in

class State(Enum):
    """
    打卡状态机的枚举状态定义。

    状态：
        - Start：未开始
        - ImageProcceeded：处理了图片，等待文本
        - TextProceeded：处理了文本，等待图片/文件
        - FileProceeded：处理了文件，等待文本
        - Success：打卡成功（既有文本又有图/文件）
        - Error：异常状态
    """    
    Start = 0 # 开始状态
    ImageProcceeded = 1 # 处理了图片，等待备注文本
    TextProceeded = 2 # 处理了文本，等待图片或文件
    FileProceeded = 3 # 处理了文件，等待备注文本
    Success = 4 # 打卡成功，即既有图片又有备注文本，或者既有文件又有备注
    Error = -1 # 出错状态

class StateMachine:
    """
    单个学生的打卡状态机。

    用途：
        - 追踪一名学生，在当日打卡中的状态变化
        - 判断是否成功打卡（image+text 组合）
        - 支持“影子记录”，用于处理先备注后发图片的情况

    :param name: 学生名称（多为学号）
    :type name: str
    :param state: 初始状态
    :type state: State
    """
    def __init__(self, name: str, state: State = State.Start):
        """
        初始化状态机。

        :param name: 名称
        :type name: str
        :param state: 初始状态
        :type state: State
        """        
        self.name = name
        self.state = state
        self.successed = False #这个变量用于判断是不是打卡同学不写备注只发图片
    
    def transition(self, input: str):
        """
        根据输入类型（image/text/file）进行状态转移。

        :param input: 输入内容类型
        :type input: str
        """        
        if self.state == State.Start:
            if input == "image":
                self.state = State.ImageProcceeded
            elif input == "text":
                self.state = State.TextProceeded
            elif input == "file":
                self.state = State.FileProceeded
            else:
                self.state = State.Start
        elif self.state == State.ImageProcceeded:
            if input == "image":
                self.state = State.ImageProcceeded
            elif input == "file":
                self.state = State.FileProceeded
            elif input == "text":
                self.state = State.Success
                self.successed = True #只要打卡成功过一次，就认为曾经成功过
            else:
                self.state = State.Error
        elif self.state == State.TextProceeded:
            if input == "image":
                self.state = State.Success
                self.successed = True #只要打卡成功过一次，就认为曾经成功过
            elif input == "file":
                self.state = State.Success
                self.successed = True #只要打卡成功过一次，就认为曾经成功过
            elif input == "text":
                self.state = State.TextProceeded
            else:
                self.state = State.Error
        elif self.state == State.FileProceeded:
            if input == "image":
                self.state = State.ImageProcceeded
            elif input == "file":
                self.state = State.FileProceeded
            elif input == "text":
                self.state = State.Success
                self.successed = True #只要打卡成功过一次，就认为曾经成功过
            else:
                self.state = State.Error
        else:
            self.state = State.Error
    
    def is_succeeded(self):
        """
        判断当前是否已成功打卡。

        :return: 是否成功
        :rtype: bool
        """        
        return self.state == State.Success   

    def is_almost_succeeded(self):
        """
        判断是否“几乎成功”（只发了图片，还未发文本）。

        用于在收尾阶段补全一些只发图片不备注的学生。

        :return: 是否几乎成功
        :rtype: bool
        """        
        return (self.state == State.ImageProcceeded or self.state == State.FileProceeded) and self.successed == False

    def reset(self):
        """
        重置状态机到 Start。

        :return: None
        :rtype: None
        """        
        self.state = State.Start

class Group:
    """
    微信群打卡导出基类。

    子类需实现：
        - save_text_csv()
        - classify_check_in_from_string()

    基类实现：
        - export_group(): 清洗并抽取微信群原始消息，生成 text_msgs
        - 自动识别学号来源（昵称或文本）
        - 自动对连续图片/文件与备注进行结构化处理
        - 自动过滤非当日消息

    :param name: 群名称
    :type name: str
    :param msgs: 微信消息列表
    :type msgs: list
    :param target_date: 处理日期
    :type target_date: datetime.datetime
    :param logger: 日志对象
    :type logger: logging.Logger
    """
    def __init__(self, name: str, msgs: list = None, target_date: datetime = None, logger = None):
        """
        初始化 Group。

        :param name: 群名称
        :type name: str
        :param msgs: 消息列表
        :type msgs: list or None
        :param target_date: 目标日期
        :type target_date: datetime.datetime or None
        :param logger: 可选 logger
        :type logger: logging.Logger or None
        """        
        self.name = name
        if msgs is None:
            msgs = []
        if target_date is None:
            target_date = datetime.today()
        self.msgs = msgs
        self.target_date = target_date
        self.logger = get_logger(logger)


    def save_text_csv(self, text_msgs):
        """
        子类必须实现：保存 txt 和 csv，并返回 (summary, csv_path)。

        :param text_msgs: 提取出的文本记录
        :type text_msgs: list[str]
        :return: 统计摘要 + csv 文件路径
        :rtype: tuple[str, str]
        """        
        pass
    
    def classify_check_in_from_string(self, input_string):
        """
        子类必须实现：根据 input_string 对打卡内容分类。

        :param input_string: 文本内容
        :type input_string: str
        :return: (student_id, csv_row)
        :rtype: tuple
        """        
        pass

    def export_group(self, mapping):
        """
        将微信群消息解析成结构化的 text_msgs（打卡记录原始文本）。

        包含逻辑：
            - 根据昵称或内容识别学号
            - 自动更新 mapping 映射表
            - 过滤无效消息、@消息、自己发送的消息
            - 处理图片、文件、文本等消息形式
            - 最终返回 text_msgs 列表（用于后续统计）

        :param mapping: 微信号 → 学号 映射表
        :type mapping: dict
        :return: 原始打卡文本列表
        :rtype: list[str]
        """        
        # 因为有些家长不会把群昵称改成含有5位数字号，所以仅仅通过他们发送的备注信息，来判断他们可能是哪位学生的家长
        # 先过一遍，找到那些群昵称里没有学号的家长们，先建立一个映射
        for m in self.msgs:
            if (is_text_message(m) and (not is_self_or_bot_message(m)) and '@' not in m.content) or \
            ((is_pic_message(m) or is_file_message(m) or is_memo_message(m) or is_video_message(m) or is_audio_message(m)) and not is_self_or_bot_message(m)):
                student_id1, _ = get_student_id_from_string(get_sender_name(m))
                student_id2, _ = get_student_id_from_string(m.content)
                # 看谁在群里说话，如果说话的人有含学号的群昵称，则refresh mapping
                # 注：mapping 的 key 存的是 m.sender。
                # ⚠️ 已知瑕疵：微信 3.x 上 sender 是「微信昵称」，而微信 4.x 上
                # sender 其实是「群昵称」（非微信名），两者语义不同却混着存。
                # 决定混存：信息多总比没有好，风险最小，总能查到对应学号。
                # 学号提取用 get_sender_name(m)（优先 sender_remark 群昵称）。
                if student_id1:
                    id = mapping.get(m.sender)
                    if not id:
                        mapping[m.sender] = student_id1
                    elif int(id) != int(student_id1):
                        # 下面这种情况是异常情况，一个家长的孩子换学号了？
                        # 不更新mapping
                        raise Exception(f"学生{id}和学生{student_id1}的家长微信竟然同名{m.sender}，请确认！")
                else:
                    if student_id2:
                        id = mapping.get(m.sender)
                        if not id:
                            mapping[m.sender] = student_id2
                        elif int(id) != int(student_id2):
                            # 下面这种情况是异常情况，只能说这次通话该家长可能是笔误了
                            self.logger.info(f"{m.sender}有可能笔误，{id}可能在帮{student_id2}打卡，请确认！")
                            # 不更新mapping

        # 真正运行提取有用的微信记录给后面的打卡程序判断处理
        text_msgs = []
        days = -1  # 第一个时间标签之前的所有消息视为旧消息，全部丢掉
        # 检查图片发送者昵称以及文本消息
        # 从旧到新遍历：微信窗口中时间标签在上、消息在下，旧→新顺序下先遇到时间标签再遇到其覆盖的消息
        for m in reversed(self.msgs):
            if is_time_message(m):
                days = compare_message_date(m, self.target_date)
                continue
            # 只处理目标日期的消息
            if days != 0:
                continue
            # 记录规则：
            # 如果发送的是一张图片，
            #    如果是自己发的，打卡不成功
            #    否则，如果是某人发的，则记录下来该发送者的昵称
            #        如果该发送者昵称不含有效学号，打卡不成功
            #        否则，即该发送者昵称含有有效学号，打卡成功
            # 否则，如果发送的是一句文本对话，
            #    如果是自己发的，打卡不成功
            #    否则，如果是某人发的，但是发送内容里含有@，表示的是@别人，打卡不成功
            #    否则，如果发送者昵称里没有含有有效学号，且发送内容里也不含有有效学号，则打卡不成功
            #    否则，如果发送者昵称里没有含有有效学号，但发送内容里含有有效学号，则打卡成功
            #    否则，如果发送者昵称里含有有效学号，但发送内容里没有有效学号，或者发送内容里含有有效学号但不一致，则打卡不成功
            #    否则打卡成功
            # 否则，如果发送的是其它内容微信，则忽略，打卡不成功

            # 如果是一个文本对话，就记录下来
            if is_text_message(m):
                # 不能是程序自己发的话，且也不能是@开始@别人的话
                if not is_self_or_bot_message(m) and not m.content.startswith('@'):
                    # 比较一下某个群昵称的话里的学号，是不是与群昵称里的学号一致，不允许帮打止的情况发生
                    # 如果某人群昵称没有学号，只有讲的话里有学号信息，对话就加进来
                    # 或如果某人群昵称有学号，只有讲的话里有学号信息且等于昵称里学号信息，对话才加进来
                    student_id1, _ = get_student_id_from_string(get_sender_name(m))
                    if not student_id1:
                        student_id1 = mapping.get(m.sender, "")

                    student_id2, _ = get_student_id_from_string(m.content)
                    if not student_id1:
                        if student_id2:
                            text_msgs.append(m.content)
                    else:
                        if student_id2 and int(student_id1) == int(student_id2):
                            text_msgs.append(m.content)
                        # 如果昵称当中没有学号信息，mapping库中没有学号信息，且讲的话中也没有学号信息，则该条文本消息是个无效消息

            # 如果是一张图片或文件，也记录下来
            elif is_pic_message(m) or is_file_message(m) or is_memo_message(m) or is_video_message(m) or is_audio_message(m):
                if not is_self_or_bot_message(m):
                    # 如果sender_remark里不含有学号信息，则用前面建立的映射表替代群昵称，否则就用群昵称
                    student_id1, _ = get_student_id_from_string(get_sender_name(m))
                    if student_id1:
                        text_msgs.append(student_id1 + m.content)
                    else:
                        student_id1 = mapping.get(m.sender)
                        if student_id1:
                            text_msgs.append(student_id1 + m.content)
                        else:
                            # 如果发的图片/文件/视频/音频/笔记等消息后，昵称没有学号信息，mapping库中没有学号信息，则该条消息后续是个无效消息
                            # 不会被打卡程序进一步处理，但是这里我们导出时还是暂时导出了。这里应该发个警告
                            text_msgs.append(get_sender_name(m) + m.content)
                            self.logger.error(f"从昵称{get_sender_name(m)}中无法得知TA在为谁打卡。")
                            self.logger.error(f"亦或者{get_sender_name(m)}不经意中已修改了昵称，但改之后还从没有透露过TA帮哪位学生打卡，请确认！！")

        return text_msgs 


class EnglishGroup(Group):
    """
    英语打卡群的特化版本。

    特点：
        - 三类打卡：单词词组默写 / 课文背诵 / 语法知识点学习
        - 使用状态机确保“图 + 文”组合被正确识别
        - 使用 shadow_records 支持“先备注→后图片”场景
        - 生成英语群专用的统计 csv 文件
    """
    def __init__(self, name="英语群", msgs: list = None, target_date: datetime = None, logger = None):
        """
        初始化英语群。

        :param name: 群名
        :type name: str
        :param msgs: 消息列表
        :type msgs: list or None
        :param target_date: 目标日期
        :type target_date: datetime.datetime or None
        :param logger: 日志器
        :type logger: logging.Logger or None
        """        
        super().__init__(name, msgs, target_date, logger)

    def update_student_records(self, student_records, student_id, word_memorization, text_recitation, grammar_study):
        """
        更新英语三类打卡记录（取当天最大值）。

        :param student_records: 学号 → [w, t, g, media]
        :type student_records: dict
        :param student_id: 学号
        :type student_id: int
        :param word_memorization: 单词词组默写
        :type word_memorization: int
        :param text_recitation: 课文背诵
        :type text_recitation: int
        :param grammar_study: 语法知识点学习
        :type grammar_study: int
        """        
        # 更新为文字打卡结果（取最大）
        student_records[student_id][0] = max(student_records[student_id][0], word_memorization)
        student_records[student_id][1] = max(student_records[student_id][1], text_recitation)
        student_records[student_id][2] = max(student_records[student_id][2], grammar_study)


    def save_text_csv(self, text_msgs):
        """
        生成英语群 txt/csv 文件，并返回 (summary, csv_path)。

        流程：
            - 构建状态机
            - 处理 shadow_records
            - 自动识别 almost-succeeded 学生
            - 统计三类打卡类别
            - 输出 sorted CSV

        :param text_msgs: 文本列表
        :type text_msgs: list[str]
        :return: (统计摘要, csv 文件路径)
        :rtype: tuple[str, str]
        """        
        nums = create_number_set()
        nums_length = len(nums)
        target_date = self.target_date.strftime("%Y-%m-%d")
        filename_txt = os.path.join(checkin_dir, f"{self.name}_{target_date}.txt")
        filename_csv = os.path.join(checkin_dir, f"{self.name}_{target_date}.csv")

        # 打卡状态机集合
        sm = {}
        # 存储学号和对应的打卡记录
        student_records = {}
        shadow_records = {} # 有些人会先打备注再发图片，先用一个影子暂存一下，直到状态机打卡成功了，再写到student_records中去

        for num in nums:
            student_records[num] = [0, 0, 0, 0]  # 初始化三类的计数为 [0, 0, 0, 0]
            shadow_records[num] = [0, 0, 0]
            sm[num] = StateMachine(f"{num}的英语打卡状态机")
        
        with open(filename_txt, "a", encoding="utf-8") as f1, open(filename_csv, "w", encoding="utf-8-sig") as f2:
            for text_msg in text_msgs:
                f1.write(text_msg)
                f1.write("\n")
                student_id, csv_row = self.classify_check_in_from_string(text_msg)
                
                # 只处理有效的记录
                if student_id:
                    word_memorization, text_recitation, grammar_study, media = map(int, csv_row.split(",")[1:])
                    # word_memorization, text_recitation, grammer_study这三个变量，在classify_check_in_from_string()之后只会有一个变量为1，其它两个变量为0

                    if word_memorization == 1 or text_recitation == 1 or grammar_study == 1:
                        # 暂存
                        shadow_records[student_id][0] = word_memorization
                        shadow_records[student_id][1] = text_recitation
                        shadow_records[student_id][2] = grammar_study

                    if media == 1: # 图片
                        sm[student_id].transition("image")
                    elif media == 2: # 文件
                        sm[student_id].transition("file")
                    else:
                        sm[student_id].transition("text")

                    if not sm[student_id].is_succeeded():
                        continue

                    # 处理打卡
                    # 状态机打卡一旦成功之后，就会把word_memorization, text_recitation, grammer_study中某个变量为1的值存到student_records中去
                    word_memorization = shadow_records[student_id][0]
                    text_recitation = shadow_records[student_id][1]
                    grammar_study = shadow_records[student_id][2]
                    # 把暂存的写到真正的student_records中去，commit
                    if word_memorization == 0 and text_recitation == 0 and grammar_study == 0:
                        self.logger.info(f"{student_id}没有判断出打卡类型，请确认！")

                    self.update_student_records(student_records, student_id, word_memorization, text_recitation, grammar_study)
                    # 上面这个函数，会使student_records的某个学生打卡项中有可能有多个1，因为这个学生会一天当中打几次不同各类的卡

                    nums = remove_found_numbers(student_id, nums)
                    sm[student_id].reset()

            # 最后在剩下的再检查一下，还有哪个学生是只用图片/视频/音频/文件/笔记打卡的
            # 与语文群和数学群不同的是，也是英语群中好的一面，没有家长会发打卡文件以公告
            oldnums = nums.copy()
            for num in nums:
                if sm[num].is_almost_succeeded():
                    word_memorization, text_recitation, grammar_study = 0, 0, 0
                    a, b, c = shadow_records[num]
                    if a == 1:
                        word_memorization = 1
                    elif b == 1:
                        text_recitation = 1
                    elif c == 1:
                        grammar_study = 1
                    if word_memorization == 0 and text_recitation == 0 and grammar_study == 0:
                        self.logger.info(f"{num}没有判断出打卡类型，请确认！")
                    self.update_student_records(student_records, num, word_memorization, text_recitation, grammar_study)
                    oldnums = remove_found_numbers(num, oldnums)
            nums = oldnums
      
            # 排序学号
            sorted_student_ids = sorted(student_records.keys(), key=int)
            
            f2.write("学号,当天单词词组默写次数,当天课文背诵次数,当天语法知识点学习次数\n")
            # 输出最终结果到CSV文件
            for student_id in sorted_student_ids:
                row = f"{student_id},{student_records[student_id][0]},{student_records[student_id][1]},{student_records[student_id][2]}"
                f2.write(row)
                f2.write("\n")
            
        # 未打卡的学生
        write_nums = nums_length - len(nums)
        left_nums = len(nums)
        
        message = f"✅ 共有 {write_nums} 位同学打卡"
        message = message + "\n" + f"⚠️ 还有 {left_nums} 位同学，即 {sorted(nums)} 可能没打卡"
        return message, filename_csv


    def classify_check_in_from_string(self, input_string):
        """
        将一条文本消息归类到三类打卡中。

        返回格式：
            (student_id, "id, w, t, g, media")

        :param input_string: 输入文本
        :type input_string: str
        :return: (student_id, csv_row)
        :rtype: tuple
        """        
        student_id, check_in = get_student_id_from_string(input_string)
        if not student_id:
            return 0, check_in
        
        # 初始化三个类别的计数为0
        word_memorization = 0
        text_recitation = 0
        grammar_study = 0
        media = 0 # text
        if "图片" in check_in or "视频" in check_in or "语音" in check_in: # 增加了【视频】【语音】
            media = 1 # image
        elif "文件" in check_in or "笔记" in check_in: # 增加了【笔记】
            media = 2 # file

        # 归类优先级：1. 课文背诵类，2. 语法知识点学习类，3. 单词词组默写类
        # 课文背诵类
        if media == 1 and ("视频" in check_in or "语音" in check_in):
            text_recitation = 1
        elif "课文" in check_in or "背诵" in check_in or "背默" in check_in:
            text_recitation = 1
        # 语法知识点学习类
        elif any(keyword in check_in for keyword in ["语法", "笔记", "作业", "语言点", "拓展", "卷子", "阅读", "完形", "完形填空", "选词填空", "知识点", "题目"]):
            grammar_study = 1
        # 单词词组默写类
        elif any(keyword in check_in for keyword in ["单词", "词组", "词汇", "词语", "默写", "听写", "动词变形", "动词不规则", "重默", "翻译"]):
            word_memorization = 1
        # 默认归类为单词词组默写类（如果没有匹配）
        else:
            # 随机取个1，其它两个则为0的分类
            values = [0, 0, 0]
            values[random.randint(0, 2)] = 1
            word_memorization = values[0]
            text_recitation = values[1]
            grammar_study = values[2]

        # 返回学号和四元组字符串（含图片判断位）
        return int(student_id), f"{student_id}, {word_memorization}, {text_recitation}, {grammar_study}, {media}"


class MathsGroup(Group):
    """
    数学（与语文共享）打卡群处理逻辑。

    特点：
        - 简单二类判定（打卡 / 未打卡）
        - 结合历史 Excel 用于累计统计
        - 支持“语文群”与“数学群”共用逻辑
    """
    def __init__(self, name="数学群", msgs: list = None, target_date: datetime = None, logger = None):
        """
        初始化数学群。

        :param name: 群名称
        :type name: str
        :param msgs: 消息列表
        :type msgs: list or None
        :param target_date: 日期
        :type target_date: datetime.datetime or None
        :param logger: 日志器
        :type logger: logging.Logger or None
        """        
        super().__init__(name, msgs, target_date, logger)

    def classify_students(self, stats: dict, target: int = 21):
        """
        将学生分为“达标”和 4 档未达标组。

        :param stats: 学号 → 累计打卡次数
        :type stats: dict
        :param target: 达标标准，默认 21
        :type target: int
        :return: summary + 各档学生列表
        :rtype: tuple
        """        
        if not stats:
            return "没有数据", [], [], [], [], []

        # 1) 达标：>= target
        reached = [sid for sid, cnt in stats.items() if cnt >= target]
        reached.sort()

        # 2) 未达标：cnt < target
        below = [(sid, cnt) for sid, cnt in stats.items() if cnt < target]

        # 如果全都达标，未达标组为空，下面就不会报错
        if below:
            max_below = max(cnt for _, cnt in below)
        else:
            max_below = None  # 没有未达标的

        # 定义 4 个动态档位（基于未达标最高）
        groups = {
            max_below: [],
            (max_below - 1) if max_below is not None else None: [],
            (max_below - 2) if max_below is not None else None: [],
            (max_below - 3) if max_below is not None else None: [],
        }

        # 只把未达标的分组
        for sid, cnt in below:
            if cnt in groups:
                groups[cnt].append(sid)

        # 排序
        for key in groups:
            groups[key].sort()

        # 输出
        lines = []
        lines.append(f"迄今为止（目标 = {target}）：")

        # 达标组
        lines.append(f"打卡次数 >= {target}（{len(reached)}人）：")
        if reached:
            chunks = [reached[i:i+2] for i in range(0, len(reached), 2)]
            for chunk in chunks:
                lines.append("  " + ", ".join(str(x) for x in chunk))

        # 未达标的 4 档，不要输出
        # for score in sorted(groups.keys(), reverse=True):
        #     if score is None:
        #         continue

        #     sids = groups[score]
        #     if not sids:
        #         continue  # 自动隐藏

        #     lines.append(f"打卡次数 = {score}（{len(sids)}人）：")
        #     chunks = [sids[i:i+2] for i in range(0, len(sids), 2)]
        #     for chunk in chunks:
        #         lines.append("  " + ", ".join(str(x) for x in chunk))

        summary_str = "\n".join(lines)

        # 返回6类：summary、达标、未达标1~4档
        return (
            summary_str,
            reached,
            groups.get(max_below, []),
            groups.get(max_below - 1, []),
            groups.get(max_below - 2, []),
            groups.get(max_below - 3, [])
        )


    def save_text_csv(self, text_msgs):
        """
        生成数学群 txt/csv 文件，并返回 (summary, csv_path)。

        逻辑：
            - 读取昨日 Excel（累计统计）
            - 判断今日是否打卡
            - 更新累计打卡次数
            - 输出 csv 与 summary

        :param text_msgs: 文本列表
        :type text_msgs: list[str]
        :return: (统计摘要, csv 文件路径)
        :rtype: tuple[str, str]
        """        
        nums = create_number_set()
        nums_length = len(nums)
        target_date = self.target_date.strftime("%Y-%m-%d")
        filename_txt = os.path.join(checkin_dir, f"{self.name}_{target_date}.txt")
        filename_csv = os.path.join(checkin_dir, f"{self.name}_{target_date}.csv")

        # 为了给老师文字版累计结果
        last_excel_path = find_excel_file(
            checkin_dir, self.name, self.target_date - timedelta(days=1)
        )
        stats_instance = CheckInStats(last_excel_path)
        stats = stats_instance.load_stats()
        for num in nums:
            if not stats.get(num):
                stats[num] = 0 # 如果没有这个学生的学号，则初始化为0，一般情况下不会，打卡第一天可能会
        
        # 存储学号和对应的打卡记录，反正就是“打卡”及“没打卡”两种结果，无需暂存
        student_records = {}
        for num in nums:
            student_records[num] = [0]  # 初始化计算为[0]
        
        with open(filename_txt, "a", encoding="utf-8") as f1, open(filename_csv, "w", encoding="utf-8-sig") as f2:
            for text_msg in text_msgs:
                f1.write(text_msg)
                f1.write("\n")
                student_id, csv_row = self.classify_check_in_from_string(text_msg)

                # 只处理有效的记录
                if student_id:          
                    # 更新学号的打卡记录
                    check_in = int(csv_row.split(",")[1])
                    # 如果只有文字备注，没有发现该学生有图片，则打卡不成功
                    if check_in == 1:
                        student_records[student_id][0] = max(student_records[student_id][0], check_in)
                        nums = remove_found_numbers(student_id, nums)
            
            # 排序学号
            sorted_student_ids = sorted(student_records.keys(), key=int)
            
            f2.write(f"学号,当天{self.name[0:2]}打卡\n")
            # 输出最终结果到CSV文件
            for student_id in sorted_student_ids:
                row = f"{student_id},{student_records[student_id][0]}"

                # stats更新一下，为了给老师文字版累计结果
                stats[student_id] += student_records[student_id][0]
                
                f2.write(row)
                f2.write("\n")
            
        # 未打卡的学生
        write_nums = nums_length - len(nums)
        left_nums = len(nums)
        
        summary_str, *_ = self.classify_students(stats)

        message = f"✅ 共有 {write_nums} 位同学打卡"
        message = message + "\n" + f"⚠️ 还有 {left_nums} 位同学，即 {sorted(nums)} 可能没打卡"
        message = message + "\n\n" + f"✅{summary_str}"
        return message, filename_csv


    def classify_check_in_from_string(self, input_string):
        """
        数学群打卡分类逻辑。

        特殊规则：
            - 语文/数学群有若干学生用于群公告（不计入文件打卡）
            - 文件消息也可视为“有效打卡”（除特殊学生外）

        :param input_string: 文本
        :type input_string: str
        :return: (student_id, csv_row)
        :rtype: tuple
        """        
        excluded_students = {'29117', '29166', '29155'} # TRICKY: 在语文群和数学群中，29117, 29166和29155经常会发群公告内容，不是她们打卡

        student_id, check_in = get_student_id_from_string(input_string)
        if not student_id:
            return 0, check_in
        
        # 如果只有文字备注，没有发现该学生有图片，则打卡不成功
        ret = 0
        if "图片" in check_in or "笔记" in check_in:
            ret = 1
        # 由于某些家长会以发文件的方式打卡，这里采用tricky的方式解决
        if "文件" in check_in and not student_id in excluded_students:
            ret = 1
        # 2026/2/4 - BUGFIX:
        # 任何人可以通过发文件的方式在“数学群”和“语文群”中打卡，但是excluded_students里的家长不行，因为他们会经常发文件，公告打卡内容
        # 他们发文件程序只认为是公告，而不是打卡。他们的打卡方式只有发图片或发笔记才行
        return int(student_id), f"{student_id}, {ret}"


class GroupFactory:
    """
    根据群名创建对应 Group 的工厂类。

    支持：
        - 英语群 → EnglishGroup
        - 数学群/语文群/测试群 → MathsGroup
    """
    def create_group(self, groupname: str, msgs: list, target_date: datetime, logger = None) -> Group:
        """
        创建对应群名称的 Group 实例。

        :param groupname: 群名称
        :type groupname: str
        :param msgs: 消息列表
        :type msgs: list
        :param target_date: 日期
        :type target_date: datetime.datetime
        :param logger: logger
        :type logger: logging.Logger or None
        :return: Group 子类对象
        :rtype: Group
        """        
        if groupname == "英语群":
            return EnglishGroup(msgs=msgs, target_date=target_date, logger=logger)
        elif groupname == "数学群":
            return MathsGroup(msgs=msgs, target_date=target_date, logger=logger)
        elif groupname == "语文群":
            return MathsGroup(name=groupname, msgs=msgs, target_date=target_date, logger=logger)
        elif groupname == "测试群":
            return MathsGroup(name=groupname, msgs=msgs, target_date=target_date, logger=logger)
        else:
            raise Exception("未知的微信群")

def main():
    """
    命令行入口：导出微信群文字并统计打卡。

    功能：
        - 用户输入日期
        - 初始化各目录
        - 加载映射表
        - 获取微信窗口
        - 使用 GroupFactory 调用对应群逻辑
        - 自动生成 txt/csv 文件
        - 可选发送结果回微信群

    :return: None
    :rtype: None
    """
    check_version_or_exit(label="export")
    _build_parser().parse_known_args()
    logger = get_logger()
    # sanity check
    if not group_names or not checkin_dir:
        logger.error(f"❌ 打卡微信群和打卡工作目录没有在环境文件里设置！")
        return

    try:
        os.makedirs(checkin_dir, exist_ok=True)
    except:
        logger.error(f"❌ 创建目录{checkin_dir}失败")
        return

    # 用户输入完整日期
    yesterday_date = datetime.today() - timedelta(days=1)
    date_str = input(f"请输入要生成的日期（格式 yyyy-mm-dd 或 yyyy/mm/dd，例如 {yesterday_date.strftime('%Y-%m-%d')}）：").strip()

    # 兼容不同日期格式
    date_formats = ["%Y-%m-%d", "%Y/%m/%d", "%Y%m%d"]
    target_date = None
    for fmt in date_formats:
        try:
            target_date = datetime.strptime(date_str, fmt)
            logger.info(f"识别日期成功：{target_date}")
            break
        except Exception:
            continue

    if target_date is None:
        logger.warning("无法识别输入的日期格式，将自动使用“昨天”作为目标日期。")
        target_date = yesterday_date
  
    # 开始导出
    logger.info(f"🚀 启动微信群文字导出：{group_names}")
    try:
        wx = WeChat(ads=False, resize=False)
        hide_tkinter_root()
        logger.info("WeChat SDK 初始化成功！")
    except:
        logger.error(f"初始化 WeChat SDK 失败：{e}")
        return

    # 装载家长微信号与学生学号映射表
    mapping = {}
    try:
        wx2id = Wxname2StudentID(directory=checkin_dir)
        mapping = wx2id.load_mapping()
        logger.info(f"学生映射表加载成功，共 {len(mapping)} 条。")
    except Exception as e:
        logger.error(f"加载映射表失败，将使用空映射：{e}")

    # ------------------------------
    # 获取微信窗口
    # ------------------------------
    hwnd = safe_get_wechat_window(logger)
    if hwnd:
        safe_bring_wechat_to_front(hwnd, logger)
    move_mouse_to_chat_center()
    set_wechat_window_size(logger)

    # ------------------------------
    # 主流程：逐个处理群
    # ------------------------------
    factory = GroupFactory()
    for groupname in group_names:
        if not ask_yes_no(f"要处理{groupname}吗？", default=True):
            logger.warning(f"跳过群：{groupname}")
            continue

        if not safe_open_chat(wx, groupname, logger):
            continue  

        # 再次置顶窗口
        if hwnd:
            safe_bring_wechat_to_front(hwnd, logger)

        # 边滚动边收集消息 — 读三遍，合并去重，防止单次漏消息
        msgs1 = safe_scroll_and_collect(wx, scroll_pixel=SCROLL_PIXEL, target_date=target_date, logger=logger)
        msgs2 = safe_scroll_and_collect(wx, scroll_pixel=SCROLL_PIXEL - 50, target_date=target_date, logger=logger)
        msgs3 = safe_scroll_and_collect(wx, scroll_pixel=SCROLL_PIXEL + 50, target_date=target_date, logger=logger)
        if not msgs1 and not msgs2 and not msgs3:
            continue

        # 本群调试日志文件：屏幕的三轮 Debug 结果同步写入该文件（每次运行重新生成）
        debug_log_path = os.path.join(checkin_dir, f"{groupname}_{target_date.strftime('%Y-%m-%d')}.log")
        with open(debug_log_path, "w", encoding="utf-8"):
            pass

        # DEBUG: 打印三遍的原始消息（未过滤）
        for pass_label, msgs in [("第一遍", msgs1), ("第二遍", msgs2), ("第三遍", msgs3)]:
            debug_dump_raw_messages(groupname, pass_label, msgs, logger, log_file=debug_log_path)

        # 三遍分别创建 Group 并过滤
        group = None
        try:
            group = factory.create_group(groupname, msgs1, target_date, logger)
            text_msgs1_raw = group.export_group(mapping)
        except Exception as e:
            logger.error(f"处理群 {groupname} 第一遍出错：{e}")
            text_msgs1_raw = []

        try:
            group2 = factory.create_group(groupname, msgs2, target_date, logger)
            text_msgs2_raw = group2.export_group(mapping)
        except Exception as e:
            logger.error(f"处理群 {groupname} 第二遍出错：{e}")
            text_msgs2_raw = []

        try:
            group3 = factory.create_group(groupname, msgs3, target_date, logger)
            text_msgs3_raw = group3.export_group(mapping)
        except Exception as e:
            logger.error(f"处理群 {groupname} 第三遍出错：{e}")
            text_msgs3_raw = []

        # 如果第一遍挂了，用第二遍的 group 来 save_text_csv
        if group is None:
            group = group2
        if group is None:
            group = group3

        # 每遍各自内部去重
        seen1 = set()
        text_msgs1 = [t for t in text_msgs1_raw if t not in seen1 and not seen1.add(t)]
        seen2 = set()
        text_msgs2 = [t for t in text_msgs2_raw if t not in seen2 and not seen2.add(t)]
        seen3 = set()
        text_msgs3 = [t for t in text_msgs3_raw if t not in seen3 and not seen3.add(t)]

        # 合并三遍结果（去重，保持顺序）
        seen = set()
        text_msgs = []
        for t in text_msgs1 + text_msgs2 + text_msgs3:
            if t not in seen:
                seen.add(t)
                text_msgs.append(t)
        logger.info(f"{groupname} 三遍合并：{len(text_msgs1)} + {len(text_msgs2)} + {len(text_msgs3)} → {len(text_msgs)} 条")

        try:
            # DEBUG: 打印 export_group 提取到的 text_msgs
            debug_dump_text_msgs(groupname, text_msgs, logger, log_file=debug_log_path)

            if text_msgs:
                summary, file_path = group.save_text_csv(text_msgs)
                logger.info(summary)

                # 用户输入是否输出到微信里
                if ask_yes_no("⚠️ 要输出结果到微信群里吗", default=True):
                    summary = f"【系统消息】 {target_date.strftime('%Y-%m-%d')} 统计结果如下：" + "\n" + summary

                    if not safe_send_msg_immediately(wx, summary, groupname, logger=logger): # 立刻发送
                        logger.error(f"❌ 发送到微信群{groupname}失败！")
                    else:
                        logger.info(f"✅ 发送到微信群{groupname}成功！")
                logger.info(f"✅ {groupname}导出完毕！")
            else:
                logger.warning(f"⚠️ 未检测到新的文字消息，请确认微信群{groupname}已打开。")
        except Exception as e:
            logger.error(f"处理群 {groupname} 发生错误：{e}")  

    wx2id.save_mapping(mapping)
    logger.info("所有群组处理已结束！")

if __name__ == "__main__":
    main()
