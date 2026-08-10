"""
车载AI助手"奔奔" - 免费假大模型服务
完全替代真实大模型，用关键词匹配返回正确的tool_calls格式
不花一分钱，也不用任何大模型API

用法：python fake_llm_server.py
然后在AI助手配置中把"服务地址"改为 http://192.168.1.54:9998/compatible-mode/v1
"""

import json
import os
import re
import time
import hashlib
from datetime import datetime
from collections import OrderedDict

HOST = "0.0.0.0"
PORT = 9998
LOG_DIR = "fake_llm_logs"
os.makedirs(LOG_DIR, exist_ok=True)


# ============================================================
# 会话状态管理 - 避免重复执行命令
# ============================================================

class SessionState:
    """管理用户会话状态，防止命令重复执行"""
    
    def __init__(self, max_sessions=100):
        self._sessions = OrderedDict()
        self._max_sessions = max_sessions
        self._confirm_keywords = {
            "打开", "开", "好的", "好", "确认", "执行", "是", "对",
            "可以", "行", "没问题", "没错", "就这个", "确定",
            "打开它", "开它", "执行吧", "就这样",
        }
        self._cancel_keywords = {
            "取消", "不用", "算了", "不要", "关掉", "停止", "取消吧",
        }
    
    def _get_session_id(self, request_body):
        """从请求中提取会话ID"""
        # 尝试从Authorization或user字段获取
        auth = request_body.get("user", "")
        if auth:
            return f"user_{auth}"
        
        # 从messages中提取session标识
        messages = request_body.get("messages", [])
        for msg in messages:
            if msg.get("role") == "system":
                content = msg.get("content", "")
                if isinstance(content, str):
                    # 尝试提取session信息
                    return f"session_{hashlib.md5(content.encode()).hexdigest()[:8]}"
        
        # 默认session
        return "default_session"
    
    def record_command(self, request_body, tool_calls):
        """记录最近的工具调用，用于后续确认"""
        session_id = self._get_session_id(request_body)
        self._sessions[session_id] = {
            "timestamp": time.time(),
            "tool_calls": tool_calls,
            "confirmed": False,
        }
        # 限制会话数量
        if len(self._sessions) > self._max_sessions:
            self._sessions.popitem(last=False)
    
    def get_pending_command(self, request_body, user_text):
        """
        检查用户文本是否为确认回复
        如果是，返回之前的工具调用
        """
        session_id = self._get_session_id(request_body)
        session = self._sessions.get(session_id)
        
        if not session:
            return None
        
        # 检查是否过期（30秒内有效）
        if time.time() - session["timestamp"] > 30:
            self._sessions.pop(session_id, None)
            return None
        
        # 如果已确认，不再返回
        if session["confirmed"]:
            return None
        
        # 检查用户文本是否为确认关键词
        text = user_text.strip()
        if text in self._confirm_keywords:
            session["confirmed"] = True
            return session["tool_calls"]
        
        # 检查是否为取消关键词
        if text in self._cancel_keywords:
            self._sessions.pop(session_id, None)
            return "cancelled"
        
        return None
    
    def is_duplicate_command(self, request_body, user_text, tool_calls):
        """
        检查是否为重复的相同命令
        避免在会话中重复执行相同的工具调用
        """
        session_id = self._get_session_id(request_body)
        session = self._sessions.get(session_id)
        
        if not session or not tool_calls:
            return False
        
        # 比较工具调用内容是否相同
        import json as json_mod
        new_calls_json = json_mod.dumps(
            [{"name": tc["function"]["name"], "args": tc["function"]["arguments"]} 
             for tc in tool_calls], 
            sort_keys=True
        )
        
        old_calls_json = json_mod.dumps(
            [{"name": tc["function"]["name"], "args": tc["function"]["arguments"]} 
             for tc in session["tool_calls"]],
            sort_keys=True
        )
        
        # 如果完全相同且在5秒内，视为重复
        if new_calls_json == old_calls_json and time.time() - session["timestamp"] < 5:
            return True
        
        return False


# 全局会话状态管理器
session_state = SessionState()


def save_log(filename, data):
    filepath = os.path.join(LOG_DIR, filename)
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ============================================================
# 关键词匹配规则
# ============================================================

def make_tool_call(name, arguments=None):
    """构造一个工具调用"""
    if arguments is None:
        arguments = {}
    return {
        "id": f"call_{int(time.time()*1000)}",
        "type": "function",
        "function": {
            "name": name,
            "arguments": json.dumps(arguments, ensure_ascii=False),
        },
    }


def match_navigation(user_text, available_tool_names):
    """匹配导航相关指令"""
    tool_calls = []

    # 排除温度调节场景（"温度调到24度"、"调到24度"），避免误识别为导航
    if re.search(r"(温度|空调).*?(调到|设到|设置|调至).*?度", user_text):
        return tool_calls
    if re.search(r"调到\s*\d+(\.\d+)?\s*度", user_text):
        return tool_calls

    # 搜索目的地 - 必须包含明确的导航关键词
    dest_patterns = [
        r"(?:去|回|开车去|出发去|开车到|出发到)(.{2,20}?)(?:的路线|怎么走|怎么去|哪里|哪儿|那边|这边|这里|那里|吧|呢|啊|了|$)",
        r"(?:导航到|导航去|开到|想去|要去|回到)(.{2,20}?)(?:的路线|怎么走|怎么去|吧|呢|$)",
        r"(?:去|回|到)(.{2,20}?)(?:的路线|怎么走|怎么去|吧|呢|$)",
    ]
    for pattern in dest_patterns:
        match = re.search(pattern, user_text)
        if match and "search_navigation_destination" in available_tool_names:
            destination = match.group(1).strip()
            # 排除数字开头的（很可能是温度、百分比等数值）
            if destination and len(destination) >= 2 and not re.match(r"^\d", destination):
                # 去除常见前缀和后缀
                for prefix in ["到", "去", "回"]:
                    if destination.startswith(prefix):
                        destination = destination[len(prefix):]
                for suffix in ["的路线", "怎么走", "怎么去", "那边", "这儿", "那里", "这里", "吧", "呢", "啊"]:
                    destination = destination.replace(suffix, "")
                destination = destination.strip()
                # 再次检查排除纯数字或数字开头
                if destination and not re.match(r"^\d", destination) and len(destination) >= 2:
                    tool_calls.append(make_tool_call(
                        "search_navigation_destination",
                        {"destination": destination}
                    ))
                    return tool_calls

    # 搜索附近
    nearby_patterns = [
        r"(附近|周边|旁边).*?(?:有|找|看|的|)(.{2,15}?)(?:吗|呢|啊|吧|$)",
        r"(找|搜索|看一下|看看).*?(附近|周边|旁边).*?(.{2,15}?)(?:吗|呢|啊|吧|$)",
    ]
    for pattern in nearby_patterns:
        match = re.search(pattern, user_text)
        if match and "search_navigation_nearby" in available_tool_names:
            # 提取关键词
            groups = match.groups()
            keyword = ""
            for g in groups:
                if g and g not in ["附近", "周边", "旁边", "找", "搜索", "看一下", "看看"]:
                    keyword = g.strip()
                    break
            if keyword:
                tool_calls.append(make_tool_call(
                    "search_navigation_nearby",
                    {"keyword": keyword}
                ))
                return tool_calls

    # 导航控制
    nav_actions = [
        (["开始导航", "开始路线", "导航开始"], "start"),
        (["结束导航", "停止导航", "关闭导航", "取消导航"], "end"),
        (["回家", "回家里", "我要回家"], "home"),
        (["去公司", "去上班", "回公司"], "company"),
        (["打开路况", "看路况", "显示路况"], "traffic_on"),
        (["关闭路况", "路况关掉"], "traffic_off"),
        (["放大", "放大地图", "地图放大"], "zoom_in"),
        (["缩小", "缩小地图", "地图缩小"], "zoom_out"),
        (["2D视角", "2D模式", "二维视角"], "view_2d"),
        (["3D视角", "3D模式", "三维视角", "3d视角", "3d模式"], "view_3d"),
        (["正北视角", "北向", "朝北"], "view_north"),
        (["导航静音", "关掉导航声音", "导航没声音"], "mute"),
        (["取消静音", "导航有声音", "导航声音"], "unmute"),
        (["刷新路线", "重新规划", "换条路"], "refresh_route"),
    ]
    for keywords, action in nav_actions:
        for kw in keywords:
            if kw in user_text and "control_navigation" in available_tool_names:
                tool_calls.append(make_tool_call(
                    "control_navigation",
                    {"action": action}
                ))
                return tool_calls

    return tool_calls


def match_light(user_text, available_tool_names):
    """匹配灯光相关指令"""
    tool_calls = []

    # 大灯开关 (打开/关闭大灯、车灯、前大灯)
    if re.search(r"(大灯|车灯|前大灯)", user_text) and not re.search(r"(自动|雾灯|阅读灯|氛围灯|室内灯)", user_text):
        enabled = not re.search(r"(关闭|关掉|关|灭|熄灭|取消|禁用)", user_text)
        if "set_light_state" in available_tool_names:
            tool_calls.append(make_tool_call(
                "set_light_state",
                {"enabled": enabled, "type": "headlight"}
            ))
            return tool_calls

    # 自动大灯
    if re.search(r"(自动大灯|自动灯)", user_text):
        enabled = not re.search(r"(关闭|关掉|关|取消|禁用)", user_text)
        if "set_auto_headlight" in available_tool_names:
            tool_calls.append(make_tool_call(
                "set_auto_headlight",
                {"enabled": enabled}
            ))
            return tool_calls

    # 后雾灯
    if re.search(r"(雾灯|后雾灯)", user_text):
        enabled = not re.search(r"(关闭|关掉|关|取消|禁用)", user_text)
        if "set_rear_fog_lamp" in available_tool_names:
            tool_calls.append(make_tool_call(
                "set_rear_fog_lamp",
                {"enabled": enabled}
            ))
            return tool_calls

    # 阅读灯
    if re.search(r"阅读灯", user_text):
        if re.search(r"(打开|开|点亮|亮起)", user_text):
            mode = "on"
        elif re.search(r"(关闭|关|灭掉|熄灭)", user_text):
            mode = "off"
        elif re.search(r"(开门|门灯)", user_text):
            mode = "door"
        elif re.search(r"自动", user_text):
            mode = "auto"
        else:
            mode = "on"  # 默认打开
        
        if "set_reading_light" in available_tool_names:
            tool_calls.append(make_tool_call(
                "set_reading_light",
                {"mode": mode}
            ))
            return tool_calls

    return tool_calls


def match_climate(user_text, available_tool_names):
    """匹配空调/温度相关指令"""
    tool_calls = []

    # 空调开关
    if re.search(r"(空调|冷气|冷风|暖风)", user_text) and not re.search(r"(温度|风量|风|循环|出风|除霜|净化)", user_text):
        if re.search(r"(关闭|关掉|关|停止|取消)", user_text):
            if "set_climate_power" in available_tool_names:
                tool_calls.append(make_tool_call("set_climate_power", {"enabled": False}))
                return tool_calls
        elif re.search(r"(打开|开|开启|启动)", user_text):
            if "set_climate_power" in available_tool_names:
                tool_calls.append(make_tool_call("set_climate_power", {"enabled": True}))
                return tool_calls

    # 自动空调
    if re.search(r"(自动空调|空调自动)", user_text):
        enabled = not re.search(r"(关闭|关掉|关|取消)", user_text)
        if "set_climate_auto" in available_tool_names:
            tool_calls.append(make_tool_call("set_climate_auto", {"enabled": enabled}))
            return tool_calls

    # 温度调节 - 支持多种表达和带空格的数字
    # 匹配: "温度调到24度"、"温度24度"、"空调温度调到 24 度"、"把温度设到22.5度"、"调到25度"
    temp_match = re.search(r"(?:温度|空调温度).*?(?:调到|设到|设为|调至|设置到|设置为|到)?\s*(\d+(?:\.\d+)?)\s*度", user_text)
    if not temp_match:
        # 单独的"调到N度"格式
        temp_match = re.search(r"(?:调到|设到|设为|调至)\s*(\d+(?:\.\d+)?)\s*度", user_text)
    if temp_match and "control_climate_temperature" in available_tool_names:
        temp = float(temp_match.group(1))
        if 16 <= temp <= 32:
            # 判断目标区域
            target = "dual"
            if re.search(r"(主驾|驾驶员|左边)", user_text):
                target = "driver"
            elif re.search(r"(副驾|右边)", user_text):
                target = "passenger"
            tool_calls.append(make_tool_call(
                "control_climate_temperature",
                {"action": "set", "temperature_celsius": temp, "target": target}
            ))
            return tool_calls
        else:
            # 温度超出范围，返回提示
            return None  # 让上层返回普通文本

    temp_up = re.search(r"(温度调高|升高温度|温度升高|温度加|温度上升|调高温度|热点|热一点|暖一点|暖和点)", user_text)
    if temp_up and "control_climate_temperature" in available_tool_names:
        tool_calls.append(make_tool_call("control_climate_temperature", {"action": "increase"}))
        return tool_calls

    temp_down = re.search(r"(温度调低|降低温度|温度降低|温度减|温度下降|调低温度|凉点|凉一点|冷一点|凉快一点)", user_text)
    if temp_down and "control_climate_temperature" in available_tool_names:
        tool_calls.append(make_tool_call("control_climate_temperature", {"action": "decrease"}))
        return tool_calls

    # 风量调节
    fan_match = re.search(r"(?:风量|风力|风).*?(\d+)\s*(?:级|档)?", user_text)
    if fan_match and "control_climate_fan" in available_tool_names:
        level = int(fan_match.group(1))
        if 0 <= level <= 10:
            tool_calls.append(make_tool_call(
                "control_climate_fan",
                {"action": "set", "level": level}
            ))
            return tool_calls

    fan_up = re.search(r"(风量调大|风量加大|风大点|风大一点|风力加大|风量加)", user_text)
    if fan_up and "control_climate_fan" in available_tool_names:
        tool_calls.append(make_tool_call("control_climate_fan", {"action": "increase"}))
        return tool_calls

    fan_down = re.search(r"(风量调小|风量减小|风小点|风小一点|风力减小|风量减)", user_text)
    if fan_down and "control_climate_fan" in available_tool_names:
        tool_calls.append(make_tool_call("control_climate_fan", {"action": "decrease"}))
        return tool_calls

    # 循环模式
    if re.search(r"(内循环|内部循环)", user_text) and "set_climate_circulation" in available_tool_names:
        tool_calls.append(make_tool_call("set_climate_circulation", {"mode": "inside"}))
        return tool_calls
    if re.search(r"(外循环|外部循环)", user_text) and "set_climate_circulation" in available_tool_names:
        tool_calls.append(make_tool_call("set_climate_circulation", {"mode": "outside"}))
        return tool_calls
    if re.search(r"(自动循环|循环自动)", user_text) and "set_climate_circulation" in available_tool_names:
        tool_calls.append(make_tool_call("set_climate_circulation", {"mode": "auto"}))
        return tool_calls

    # 出风模式
    if re.search(r"(吹脸|吹脸模式|迎面|迎面吹风)", user_text) and "set_climate_airflow" in available_tool_names:
        tool_calls.append(make_tool_call("set_climate_airflow", {"mode": "face"}))
        return tool_calls
    if re.search(r"(吹脚|吹脚模式|脚下)", user_text) and "set_climate_airflow" in available_tool_names:
        tool_calls.append(make_tool_call("set_climate_airflow", {"mode": "feet"}))
        return tool_calls
    if re.search(r"(吹前挡|吹玻璃|前挡|挡风玻璃)", user_text) and "set_climate_airflow" in available_tool_names:
        tool_calls.append(make_tool_call("set_climate_airflow", {"mode": "windshield"}))
        return tool_calls

    # 除霜
    if re.search(r"(除霜|去雾|除雾|前挡除霜|前挡除雾)", user_text):
        enabled = not re.search(r"(关闭|关掉|关|取消)", user_text)
        if "set_front_defrost" in available_tool_names:
            tool_calls.append(make_tool_call("set_front_defrost", {"enabled": enabled}))
            return tool_calls

    # 空气净化
    if re.search(r"(空气净化|净化|负离子|pm2.5|PM2\.5)", user_text):
        enabled = not re.search(r"(关闭|关掉|关|取消)", user_text)
        if "set_air_purification" in available_tool_names:
            tool_calls.append(make_tool_call("set_air_purification", {"enabled": enabled}))
            return tool_calls

    return tool_calls


def match_window(user_text, available_tool_names):
    """匹配车窗/天窗/遮阳帘相关指令"""
    tool_calls = []

    # 车窗目标映射
    target_map = {
        "全部车窗": "all", "所有车窗": "all", "车窗全部": "all",
        "主驾车窗": "front_left", "主驾": "front_left", "左前": "front_left",
        "副驾车窗": "front_right", "副驾": "front_right", "右前": "front_right",
        "左后车窗": "rear_left", "左后": "rear_left",
        "右后车窗": "rear_right", "右后": "rear_right",
    }

    # 位置百分比
    pos_match = re.search(r"(?:车窗|窗户).*?(\d+)\s*%", user_text)

    # 判断是否是车窗控制
    is_window = re.search(r"(车窗|窗户|玻璃)", user_text) and not re.search(r"(前挡|后挡|天窗|遮阳帘)", user_text)
    if is_window and "control_windows" in available_tool_names:
        target = "all"
        for kw, t in target_map.items():
            if kw in user_text:
                target = t
                break

        if pos_match:
            percent = int(pos_match.group(1))
            percent = max(0, min(100, percent))
            tool_calls.append(make_tool_call(
                "control_windows",
                {"target": target, "action": "set_position", "position_percent": percent}
            ))
            return tool_calls

        if re.search(r"(打开|开|降下|摇下|放下)", user_text):
            if re.search(r"(通风|透气|留一条缝)", user_text):
                action = "vent"
            else:
                action = "open"
            tool_calls.append(make_tool_call("control_windows", {"target": target, "action": action}))
            return tool_calls

        if re.search(r"(关闭|关|关上|升起|摇上|升上去)", user_text):
            tool_calls.append(make_tool_call("control_windows", {"target": target, "action": "close"}))
            return tool_calls

    # 天窗
    if re.search(r"(天窗)", user_text) and "control_sunroof" in available_tool_names:
        sunroof_pos = re.search(r"天窗.*?(\d+)\s*%", user_text)
        if sunroof_pos:
            percent = int(sunroof_pos.group(1))
            percent = max(0, min(100, percent))
            # 按5%步进
            percent = round(percent / 5) * 5
            tool_calls.append(make_tool_call(
                "control_sunroof",
                {"action": "set_position", "position_percent": percent}
            ))
            return tool_calls

        if re.search(r"(打开|开|推开)", user_text):
            if re.search(r"(通风|透气|翘起来)", user_text):
                action = "vent"
            else:
                action = "open"
            tool_calls.append(make_tool_call("control_sunroof", {"action": action}))
            return tool_calls

        if re.search(r"(关闭|关|关上)", user_text):
            tool_calls.append(make_tool_call("control_sunroof", {"action": "close"}))
            return tool_calls

        if re.search(r"(暂停|停住|停下|停止)", user_text):
            tool_calls.append(make_tool_call("control_sunroof", {"action": "pause"}))
            return tool_calls

    # 遮阳帘
    if re.search(r"(遮阳帘|窗帘|遮光帘)", user_text) and "control_curtain" in available_tool_names:
        curtain_pos = re.search(r"(?:遮阳帘|窗帘).*?(\d+)\s*%", user_text)
        if curtain_pos:
            percent = int(curtain_pos.group(1))
            percent = max(0, min(100, percent))
            percent = round(percent / 5) * 5
            tool_calls.append(make_tool_call(
                "control_curtain",
                {"action": "set_position", "position_percent": percent}
            ))
            return tool_calls

        if re.search(r"(打开|开|拉开|收起)", user_text):
            tool_calls.append(make_tool_call("control_curtain", {"action": "open"}))
            return tool_calls

        if re.search(r"(关闭|关|关上|拉上|展开)", user_text):
            tool_calls.append(make_tool_call("control_curtain", {"action": "close"}))
            return tool_calls

        if re.search(r"(暂停|停住|停下|停止)", user_text):
            tool_calls.append(make_tool_call("control_curtain", {"action": "pause"}))
            return tool_calls

    return tool_calls


def match_vehicle_control(user_text, available_tool_names):
    """匹配车辆控制（后视镜、驾驶模式、能量回收等）"""
    tool_calls = []

    # 后视镜折叠
    if re.search(r"(后视镜|倒车镜)", user_text):
        if "set_mirror_fold" in available_tool_names:
            folded = bool(re.search(r"(折叠|收起来|收回|合上)", user_text))
            tool_calls.append(make_tool_call("set_mirror_fold", {"folded": folded}))
            return tool_calls

    # 驾驶模式
    drive_modes = [
        (["单踏板", "单踏板模式"], "single_pedal"),
        (["经济模式", "节能模式", "eco模式", "ECO"], "eco"),
        (["运动模式", "sport模式", "SPORT"], "sport"),
        (["舒适模式", "comfort模式", "COMFORT", "标准模式", "普通模式"], "comfort"),
    ]
    for keywords, mode in drive_modes:
        for kw in keywords:
            if kw in user_text and "set_drive_mode" in available_tool_names:
                tool_calls.append(make_tool_call("set_drive_mode", {"mode": mode}))
                return tool_calls

    # 能量回收
    recovery_levels = [
        (["能量回收弱", "回收弱", "弱回收"], "weak"),
        (["能量回收中", "回收中", "中回收"], "medium"),
        (["能量回收强", "回收强", "强回收"], "strong"),
    ]
    for keywords, level in recovery_levels:
        for kw in keywords:
            if kw in user_text and "set_energy_recovery" in available_tool_names:
                tool_calls.append(make_tool_call("set_energy_recovery", {"level": level}))
                return tool_calls

    if re.search(r"(能量回收|动能回收)", user_text):
        if "set_energy_recovery" in available_tool_names:
            level = "medium"
            if re.search(r"(低|小|弱)", user_text):
                level = "weak"
            elif re.search(r"(高|大|强)", user_text):
                level = "strong"
            tool_calls.append(make_tool_call("set_energy_recovery", {"level": level}))
            return tool_calls

    return tool_calls


def match_seat(user_text, available_tool_names):
    """匹配座椅相关指令"""
    tool_calls = []
    
    # 主驾/副驾判断
    is_driver = not re.search(r"(副驾|副座|副驾驶)", user_text)
    position = "driver" if is_driver else "passenger"
    
    # 座椅加热
    if re.search(r"(座椅加热|加热座椅)", user_text):
        level = 0
        if re.search(r"(关闭|关掉|关|停止|取消)", user_text):
            level = 0
        elif re.search(r"(三档|3档|最大|最强)", user_text):
            level = 3
        elif re.search(r"(二档|2档)", user_text):
            level = 2
        elif re.search(r"(一档|1档|打开|开启|开)", user_text):
            level = 1
        elif re.search(r"(座椅加热)", user_text):
            level = 1
        
        if "set_seat_heating" in available_tool_names:
            tool_calls.append(make_tool_call(
                "set_seat_heating",
                {"position": position, "level": level}
            ))
            return tool_calls

    # 座椅通风
    if re.search(r"(座椅通风|通风座椅)", user_text):
        level = 0
        if re.search(r"(关闭|关掉|关|停止|取消)", user_text):
            level = 0
        elif re.search(r"(三档|3档|最大|最强)", user_text):
            level = 3
        elif re.search(r"(二档|2档)", user_text):
            level = 2
        elif re.search(r"(一档|1档|打开|开启|开)", user_text):
            level = 1
        
        if "set_seat_ventilation" in available_tool_names:
            tool_calls.append(make_tool_call(
                "set_seat_ventilation",
                {"position": position, "level": level}
            ))
            return tool_calls

    # 座椅按摩
    if re.search(r"(座椅按摩|按摩)", user_text):
        if re.search(r"(关闭|关掉|关|停止|取消|没|没关)", user_text):
            # 关闭按摩
            if "set_seat_heating" in available_tool_names:
                # 用加热工具的关闭来模拟（因为没有专门的按摩工具）
                tool_calls.append(make_tool_call(
                    "set_seat_heating",
                    {"position": position, "level": 0}
                ))
                return tool_calls
        else:
            # 打开按摩
            if "set_seat_heating" in available_tool_names:
                tool_calls.append(make_tool_call(
                    "set_seat_heating",
                    {"position": position, "level": 2}
                ))
                return tool_calls

    # 座椅位置/坐姿
    if re.search(r"(座椅.*位置|调座椅|调一下座椅|调整座椅|坐姿)", user_text):
        if re.search(r"(向前|往前|前进)", user_text):
            direction = "forward"
        elif re.search(r"(向后|往后|后退)", user_text):
            direction = "backward"
        elif re.search(r"(向上|往上)", user_text):
            direction = "up"
        elif re.search(r"(向下|往下)", user_text):
            direction = "down"
        elif re.search(r"(升高|调高)", user_text):
            direction = "raise"
        elif re.search(r"(降低|调低)", user_text):
            direction = "lower"
        else:
            direction = "forward"
        
        if "set_seat_position" in available_tool_names:
            tool_calls.append(make_tool_call(
                "set_seat_position",
                {"position": position, "direction": direction}
            ))
            return tool_calls

    return tool_calls


def match_display(user_text, available_tool_names):
    """匹配屏幕/显示相关指令"""
    tool_calls = []

    # 自动亮度
    if re.search(r"(自动亮度)", user_text) and "set_auto_brightness" in available_tool_names:
        enabled = not re.search(r"(关闭|关掉|关|取消)", user_text)
        target = "all"
        if re.search(r"(主屏|主屏幕)", user_text):
            target = "main"
        elif re.search(r"(副屏|副驾屏)", user_text):
            target = "passenger"
        elif re.search(r"(仪表|仪表盘)", user_text):
            target = "instrument"
        tool_calls.append(make_tool_call("set_auto_brightness", {"target": target, "enabled": enabled}))
        return tool_calls

    # 亮度设置
    brightness_match = re.search(r"(?:亮度).*?(\d+)\s*%", user_text)
    if brightness_match and "set_display_brightness" in available_tool_names:
        percent = int(brightness_match.group(1))
        percent = max(0, min(100, percent))
        target = "main"
        if re.search(r"(仪表|仪表盘)", user_text):
            target = "instrument"
        elif re.search(r"(副屏|副驾屏)", user_text):
            target = "passenger"
        tool_calls.append(make_tool_call(
            "set_display_brightness",
            {"target": target, "percent": percent}
        ))
        return tool_calls

    # 屏幕清洁模式
    if re.search(r"(屏幕清洁|清洁屏幕|擦屏幕)", user_text) and "set_screen_cleaning" in available_tool_names:
        enabled = not re.search(r"(关闭|退出|结束|完成|好了)", user_text)
        tool_calls.append(make_tool_call("set_screen_cleaning", {"enabled": enabled}))
        return tool_calls

    # 副屏控制
    if re.search(r"(副屏|副驾屏)", user_text) and "control_passenger_screen" in available_tool_names:
        if re.search(r"(打开|开启|开)", user_text):
            action = "on"
        elif re.search(r"(关闭|关掉|关)", user_text):
            action = "off"
        elif re.search(r"(切换|换一下|切一下)", user_text):
            action = "toggle"
        else:
            return tool_calls
        tool_calls.append(make_tool_call("control_passenger_screen", {"action": action}))
        return tool_calls

    # 主屏控制
    if re.search(r"(主屏|主屏幕)", user_text) and "control_main_screen" in available_tool_names:
        if re.search(r"(打开|开启|开)", user_text):
            action = "on"
        elif re.search(r"(关闭|关掉|关)", user_text):
            action = "off"
        elif re.search(r"(切换|换一下|切一下)", user_text):
            action = "toggle"
        else:
            return tool_calls
        tool_calls.append(make_tool_call("control_main_screen", {"action": action}))
        return tool_calls

    return tool_calls


def match_volume(user_text, available_tool_names):
    """匹配音量相关指令"""
    tool_calls = []

    context = "media"
    if re.search(r"(导航|地图)", user_text):
        context = "navigation"
    elif re.search(r"(语音|小助手|助手)", user_text):
        context = "voice"
    elif re.search(r"(通话|电话)", user_text):
        context = "call"
    elif re.search(r"(系统)", user_text):
        context = "system"

    if "control_vehicle_volume" not in available_tool_names:
        return tool_calls

    # 音量设置
    vol_match = re.search(r"(?:音量|声音).*?(\d+)\s*%", user_text)
    if vol_match:
        percent = int(vol_match.group(1))
        percent = max(0, min(100, percent))
        tool_calls.append(make_tool_call(
            "control_vehicle_volume",
            {"context": context, "action": "set", "percent": percent}
        ))
        return tool_calls

    if re.search(r"(音量加大|音量加|声音大|声音加大|调大音量|音量调大|声音调大)", user_text):
        tool_calls.append(make_tool_call(
            "control_vehicle_volume",
            {"context": context, "action": "increase"}
        ))
        return tool_calls

    if re.search(r"(音量减小|音量减|声音小|声音减小|调小音量|音量调小|声音调小)", user_text):
        tool_calls.append(make_tool_call(
            "control_vehicle_volume",
            {"context": context, "action": "decrease"}
        ))
        return tool_calls

    if re.search(r"(静音|没声音|关掉声音|关闭声音|静音模式)", user_text):
        # 检查是否是媒体静音（特定工具）
        if context == "media" and "set_media_mute" in available_tool_names:
            tool_calls.append(make_tool_call("set_media_mute", {"enabled": True}))
        else:
            tool_calls.append(make_tool_call(
                "control_vehicle_volume",
                {"context": context, "action": "mute"}
            ))
        return tool_calls

    if re.search(r"(取消静音|取消静音模式|有声音|打开声音|开启声音)", user_text):
        if context == "media" and "set_media_mute" in available_tool_names:
            tool_calls.append(make_tool_call("set_media_mute", {"enabled": False}))
        else:
            tool_calls.append(make_tool_call(
                "control_vehicle_volume",
                {"context": context, "action": "unmute"}
            ))
        return tool_calls

    return tool_calls


def match_media(user_text, available_tool_names):
    """匹配媒体控制"""
    tool_calls = []

    if "control_media" not in available_tool_names:
        return tool_calls

    # 打开音乐/播放音乐
    if re.search(r"(打开音乐|开音乐|播放音乐|放音乐|放歌|来首歌|来首音乐)", user_text):
        tool_calls.append(make_tool_call("control_media", {"action": "play"}))
        return tool_calls

    # 打开应用/打开APP
    if re.search(r"(打开应用|开应用|打开APP|开APP)", user_text):
        # 如果指定了具体应用
        app_match = re.search(r"(打开|开)(.+?)(应用|APP|app)", user_text)
        if app_match:
            app_name = app_match.group(2).strip()
            tool_calls.append(make_tool_call("control_media", {"action": "open_app", "app_name": app_name}))
        else:
            tool_calls.append(make_tool_call("control_media", {"action": "open_media"}))
        return tool_calls

    # 播放控制
    if re.search(r"(播放|开始播放|继续播放)", user_text):
        tool_calls.append(make_tool_call("control_media", {"action": "play"}))
        return tool_calls

    if re.search(r"(暂停|停止播放|停下|别唱了|先别播)", user_text):
        tool_calls.append(make_tool_call("control_media", {"action": "pause"}))
        return tool_calls

    if re.search(r"(播放暂停切换|暂停播放切换|播放暂停)", user_text):
        tool_calls.append(make_tool_call("control_media", {"action": "toggle_play_pause"}))
        return tool_calls

    if re.search(r"(上一首|上一首歌|上一曲|上一个)", user_text):
        tool_calls.append(make_tool_call("control_media", {"action": "previous"}))
        return tool_calls

    if re.search(r"(下一首|下一首歌|下一曲|换一首|下一个)", user_text):
        tool_calls.append(make_tool_call("control_media", {"action": "next"}))
        return tool_calls

    return tool_calls


def match_status_read(user_text, available_tool_names):
    """匹配状态读取指令（询问当前情况）"""
    tool_calls = []

    # 行驶状态
    if re.search(r"(档位|什么档|几档|车速|速度|多少码|电源状态|打火状态|启动状态)", user_text):
        if "get_driving_status" in available_tool_names:
            tool_calls.append(make_tool_call("get_driving_status"))
        return tool_calls

    # 车门状态
    if re.search(r"(车门|门.*有没有?关|后备箱|尾门)", user_text):
        if "get_door_status" in available_tool_names:
            tool_calls.append(make_tool_call("get_door_status"))
        return tool_calls

    # 车窗状态
    if re.search(r"(车窗.*状态|窗户.*状态|车窗.*关没|窗户.*关没|车窗.*位置|窗户.*位置)", user_text):
        if "get_window_status" in available_tool_names:
            tool_calls.append(make_tool_call("get_window_status"))
        return tool_calls

    # 天窗状态
    if re.search(r"(天窗.*状态|天窗.*关没|天窗.*位置)", user_text):
        if "get_sunroof_status" in available_tool_names:
            tool_calls.append(make_tool_call("get_sunroof_status"))
        return tool_calls

    # 遮阳帘状态
    if re.search(r"(遮阳帘.*状态|遮阳帘.*关没|遮阳帘.*位置)", user_text):
        if "get_sunshade_status" in available_tool_names:
            tool_calls.append(make_tool_call("get_sunshade_status"))
        return tool_calls

    # 车锁状态
    if re.search(r"(车锁|锁车.*状态|有没有?锁|落锁)", user_text):
        if "get_lock_status" in available_tool_names:
            tool_calls.append(make_tool_call("get_lock_status"))
        return tool_calls

    # 后视镜状态
    if re.search(r"(后视镜.*状态|后视镜.*有没有?折叠)", user_text):
        if "get_mirror_status" in available_tool_names:
            tool_calls.append(make_tool_call("get_mirror_status"))
        return tool_calls

    # 座椅状态
    if re.search(r"(座椅.*状态|座椅加热|座椅通风|座椅按摩|座椅位置)", user_text):
        if "get_seat_status" in available_tool_names:
            tool_calls.append(make_tool_call("get_seat_status"))
        return tool_calls

    # 安全带状态
    if re.search(r"(安全带|保险带)", user_text):
        if "get_seat_belt_status" in available_tool_names:
            tool_calls.append(make_tool_call("get_seat_belt_status"))
        return tool_calls

    # 空调状态
    if re.search(r"(空调.*状态|空调.*开没|风量状态|出风模式|循环模式)", user_text):
        if "get_climate_status" in available_tool_names:
            tool_calls.append(make_tool_call("get_climate_status"))
        return tool_calls

    # 温度
    if re.search(r"(当前温度|现在温度|车内温度|车外温度|多少度|温度是多少)", user_text):
        if "get_temperature_status" in available_tool_names:
            tool_calls.append(make_tool_call("get_temperature_status"))
        return tool_calls

    # 电量/油量/续航
    if re.search(r"(电量|剩余电量|油量|剩余油量|续航|还能跑|能跑多少|剩余里程|里程|能耗|小电瓶|电压)", user_text):
        if "get_energy_status" in available_tool_names:
            tool_calls.append(make_tool_call("get_energy_status"))
        return tool_calls

    # 充电状态
    if re.search(r"(充电|充电桩|充电状态|有没有?充电)", user_text):
        if "get_charging_status" in available_tool_names:
            tool_calls.append(make_tool_call("get_charging_status"))
        return tool_calls

    # 轮胎状态
    if re.search(r"(胎压|轮胎压力|胎温|轮胎温度|轮胎.*状态)", user_text):
        if "get_tire_status" in available_tool_names:
            tool_calls.append(make_tool_call("get_tire_status"))
        return tool_calls

    # 灯光状态
    if re.search(r"(灯光.*状态|灯.*开没|氛围灯|阅读灯.*状态)", user_text):
        if "get_light_status" in available_tool_names:
            tool_calls.append(make_tool_call("get_light_status"))
        return tool_calls

    # 音量状态
    if re.search(r"(当前音量|音量.*状态|媒体音量)", user_text):
        if "get_sound_status" in available_tool_names:
            tool_calls.append(make_tool_call("get_sound_status"))
        return tool_calls

    # 屏幕状态
    if re.search(r"(屏幕亮度.*状态|当前亮度)", user_text):
        if "get_display_status" in available_tool_names:
            tool_calls.append(make_tool_call("get_display_status"))
        return tool_calls

    # 车辆健康
    if re.search(r"(车机版本|系统版本|CPU温度|处理器温度|内存|剩余空间|存储|网络状态|蓝牙钥匙|钥匙电量)", user_text):
        if "get_device_health" in available_tool_names:
            tool_calls.append(make_tool_call("get_device_health"))
        return tool_calls

    # 电池健康
    if re.search(r"(电池健康|动力电池健康|电池衰减|电池寿命)", user_text):
        if "get_battery_health_estimate" in available_tool_names:
            tool_calls.append(make_tool_call("get_battery_health_estimate"))
        return tool_calls

    # 车辆档案
    if re.search(r"(什么车型|车型|车辆配置|能源类型|支持.*功能|智驾芯片|功能支持)", user_text):
        if "get_vehicle_profile" in available_tool_names:
            tool_calls.append(make_tool_call("get_vehicle_profile"))
        return tool_calls

    # 当前地址
    if re.search(r"(我在.*?哪|当前位置|在什么地方|在哪里|当前地址)", user_text):
        if "get_current_address" in available_tool_names:
            tool_calls.append(make_tool_call("get_current_address"))
        return tool_calls

    # 精确定位
    if re.search(r"(经纬度|坐标|海拔|航向|定位精度|精确位置)", user_text):
        if "get_precise_location" in available_tool_names:
            tool_calls.append(make_tool_call("get_precise_location"))
        return tool_calls

    # 导航状态
    if re.search(r"(导航.*状态|有没有?在导航|导航.*进行)", user_text):
        if "get_navigation_status" in available_tool_names:
            tool_calls.append(make_tool_call("get_navigation_status"))
        return tool_calls

    return tool_calls


def match_end_conversation(user_text, available_tool_names):
    """匹配结束对话或唤醒词"""
    
    # 唤醒词/打招呼 - 返回友好回复而不是工具调用
    wake_patterns = [
        r"^(小米同学|小米|嘿.*同学|小爱同学)$",
        r"^你好$", r"^您好$", r"^在吗$", r"^在不在$",
    ]
    for pattern in wake_patterns:
        if re.match(pattern, user_text.strip()):
            return "在的，请问需要什么帮助？"

    # 结束对话
    goodbye_patterns = [
        r"^再见$", r"^拜拜$", r"^拜拜了$", r"^再见了$",
        r"^没了$", r"^没有了$", r"^没事了$", r"^好了$",
        r"^退下$", r"^退下吧$", r"^跪安$", r"^可以了$", r"^就这样$",
        r"^不用了$", r"^不用$", r"^谢谢$", r"^多谢$",
    ]
    for pattern in goodbye_patterns:
        if re.match(pattern, user_text.strip()):
            return "好的，为您服务。"

    # 抱怨类对话
    complain_patterns = [
        r"(那你.*没|没关啊|没.*啊)",
    ]
    for pattern in complain_patterns:
        if re.search(pattern, user_text.strip()):
            return "抱歉，我会再试一次。"

    return []


def match_select_result(user_text, available_tool_names):
    """匹配选择搜索结果"""
    if "select_navigation_result" not in available_tool_names:
        return []

    # "第N个"、"N号"、"选N"
    patterns = [
        r"第\s*(\d+)\s*个",
        r"(\d+)\s*号",
        r"(?:选|选择|用|就)\s*(\d+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, user_text)
        if match:
            index = int(match.group(1))
            if index >= 1:
                return [make_tool_call("select_navigation_result", {"index": index})]

    return []


def match_change_page(user_text, available_tool_names):
    """匹配翻页"""
    if "change_navigation_result_page" not in available_tool_names:
        return []

    if re.search(r"(下一页|翻页|下一个|后面)", user_text):
        return [make_tool_call("change_navigation_result_page", {"direction": "next"})]

    if re.search(r"(上一页|前一页|前面|上一个)", user_text):
        return [make_tool_call("change_navigation_result_page", {"direction": "previous"})]

    return []


# ============================================================
# 主匹配入口
# ============================================================

def process_request(user_text, available_tool_names):
    """
    根据用户文本匹配工具调用
    返回: {
        "tool_calls": list or None,
        "fallback_text": str or None,
        "match_detail": dict  # 匹配详情
    }
    """
    text = user_text.strip()
    match_detail = {
        "input_text": text,
        "matched": False,
        "matcher_used": None,
        "matcher_name": None,
        "tool_calls": [],
        "fallback_reason": None,
        "available_tools_count": 0,
    }

    # 如果没有 tools 定义，假设所有工具都可用
    if not available_tool_names:
        available_tool_names = {
            "set_climate_power", "set_climate_auto", "control_climate_temperature",
            "set_light_state", "set_auto_headlight", "set_rear_fog_lamp", "set_reading_light",
            "control_windows", "control_sunroof", "control_curtain",
            "set_window_state", "set_sunroof_state", "set_roof_state",
            "control_vehicle", "set_vehicle_power",
            "set_seat_position", "set_seat_heating", "set_seat_ventilation",
            "set_display_brightness", "set_media_volume", "set_media_mute",
            "control_vehicle_volume",
            "search_navigation_destination", "search_navigation_nearby",
            "start_navigation", "control_navigation",
            "select_result_item", "change_page",
            "read_status", "control_media",
            "get_driving_status", "get_door_status", "get_window_status",
            "get_sunroof_status", "get_sunshade_status", "get_lock_status",
            "get_mirror_status", "get_seat_status",
            "set_roof_state", "set_energy_recovery",
            "set_auto_brightness", "set_screen_cleaning", "control_passenger_screen",
            "set_media_volume",
        }
        match_detail["fallback_reason"] = "客户端未发送 tools 定义，已启用全部工具"
    
    match_detail["available_tools_count"] = len(available_tool_names)

    # 特殊处理: 连接测试请求
    if not available_tool_names or match_detail.get("fallback_reason"):
        if "连接成功" in text or "请只回复" in text:
            match_detail["matched"] = True
            match_detail["matcher_name"] = "connection_test"
            match_detail["matcher_used"] = "连接测试匹配"
            return {
                "tool_calls": None,
                "fallback_text": "连接成功",
                "match_detail": match_detail,
            }
        if "你好" in text or "在吗" in text:
            match_detail["matched"] = True
            match_detail["matcher_name"] = "greeting"
            match_detail["matcher_used"] = "问候匹配"
            return {
                "tool_calls": None,
                "fallback_text": "你好，我是奔奔，有什么可以帮你的？",
                "match_detail": match_detail,
            }

    # 按优先级匹配各类规则
    matchers = [
        ("对话结束", match_end_conversation),
        ("导航", match_navigation),
        ("结果选择", match_select_result),
        ("翻页", match_change_page),
        # 执行类命令优先（在状态读取之前）
        ("灯光", match_light),
        ("空调", match_climate),
        ("车窗", match_window),
        ("车辆控制", match_vehicle_control),
        ("座椅", match_seat),
        ("显示", match_display),
        ("音量", match_volume),
        ("媒体", match_media),
        # 状态读取在最后（只在没有匹配到执行命令时才读取状态）
        ("状态读取", match_status_read),
    ]

    for name, matcher in matchers:
        result = matcher(text, available_tool_names)
        if result:
            match_detail["matched"] = True
            match_detail["matcher_name"] = name
            match_detail["matcher_used"] = f"{name} 匹配器"
            if isinstance(result, list):
                match_detail["tool_calls"] = [
                    {"name": tc["function"]["name"], "arguments": tc["function"]["arguments"]}
                    for tc in result
                ]
                match_detail["match_result"] = "tool_calls"
                return {
                    "tool_calls": result,
                    "fallback_text": None,
                    "match_detail": match_detail,
                }
            else:
                match_detail["fallback_text"] = result
                match_detail["match_result"] = "text"
                return {
                    "tool_calls": None,
                    "fallback_text": result,
                    "match_detail": match_detail,
                }

    # 没有匹配到工具调用，返回普通文本回复
    match_detail["matched"] = False
    match_detail["match_result"] = "fallback"
    match_detail["fallback_reason"] = "未匹配到任何命令规则"
    match_detail["tried_matchers"] = [name for name, _ in matchers]
    
    fallback_texts = [
        "好的，我在听。",
        "明白了。",
        "收到。",
        "嗯。",
        "可以，你继续说。",
    ]
    import random
    return {
        "tool_calls": None,
        "fallback_text": random.choice(fallback_texts),
        "match_detail": match_detail,
    }


def build_response(request_body, is_stream=False):
    """构建响应（支持stream和非stream两种格式）
    返回: {"response_body": str, "match_detail": dict}
    """
    model = request_body.get("model", "fake-model")
    messages = request_body.get("messages", [])
    tools = request_body.get("tools", [])
    available_tool_names = set()
    for tool in tools:
        func = tool.get("function", {})
        name = func.get("name")
        if name:
            available_tool_names.add(name)

    # 提取用户最新消息
    user_text = ""
    for msg in reversed(messages):
        if msg.get("role") == "user":
            content = msg.get("content", "")
            if isinstance(content, list):
                for part in content:
                    if isinstance(part, dict) and part.get("type") == "text":
                        user_text += part.get("text", "")
            elif isinstance(content, str):
                user_text = content
            break

    # 检查是否为确认回复（会话状态管理）
    pending_result = session_state.get_pending_command(request_body, user_text)
    if pending_result == "cancelled":
        # 用户取消了之前的命令
        match_detail = {
            "input_text": user_text,
            "matched": True,
            "matcher_name": "会话管理",
            "match_result": "text",
            "tool_calls": [],
            "fallback_text": "已取消",
            "fallback_reason": "用户取消操作",
        }
        result = {
            "tool_calls": None,
            "fallback_text": "好的，已取消。",
            "match_detail": match_detail,
        }
    elif pending_result is not None:
        # 用户确认执行之前的命令
        match_detail = {
            "input_text": user_text,
            "matched": True,
            "matcher_name": "会话管理",
            "match_result": "tool_calls",
            "tool_calls": [{"name": tc["function"]["name"], "args": tc["function"]["arguments"]} for tc in pending_result],
            "fallback_text": None,
            "fallback_reason": "用户确认执行",
            "confirmed": True,
        }
        result = {
            "tool_calls": pending_result,
            "fallback_text": None,
            "match_detail": match_detail,
        }
    else:
        # 处理请求 - 新格式
        result = process_request(user_text, available_tool_names)
        tool_calls = result["tool_calls"]
        fallback_text = result["fallback_text"]
        match_detail = result["match_detail"]
        
        # 检查是否为重复命令
        if tool_calls and session_state.is_duplicate_command(request_body, user_text, tool_calls):
            # 是重复命令，返回已执行的提示
            match_detail["matched"] = True
            match_detail["match_result"] = "text"
            match_detail["fallback_text"] = "命令已收到"
            match_detail["is_duplicate"] = True
            result = {
                "tool_calls": None,
                "fallback_text": "命令已收到",
                "match_detail": match_detail,
            }
        elif tool_calls:
            # 记录新的工具调用到会话状态
            session_state.record_command(request_body, tool_calls)

    tool_calls = result["tool_calls"]
    fallback_text = result["fallback_text"]
    match_detail = result["match_detail"]

    created = int(time.time())

    if tool_calls:
        # 返回工具调用格式
        response_obj = {
            "id": f"chatcmpl-{created}",
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": tool_calls,
                    },
                    "finish_reason": "tool_calls",
                }
            ],
            "created": created,
            "model": model,
            "object": "chat.completion",
            "usage": {
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
            },
        }
    else:
        # 返回普通文本
        response_obj = {
            "id": f"chatcmpl-{created}",
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": fallback_text or "好的。",
                    },
                    "finish_reason": "stop",
                }
            ],
            "created": created,
            "model": model,
            "object": "chat.completion",
            "usage": {
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
            },
        }

    if is_stream:
        # SSE流式输出格式
        chunks = []
        if tool_calls:
            # 流式tool_calls格式比较复杂，为了兼容性，这里简化处理
            # 先发送一个文本片段（空），再发送finish
            delta_obj = {
                "role": "assistant",
                "content": "",
            }
            chunk_start = {
                "id": f"chatcmpl-{created}",
                "choices": [{
                    "index": 0,
                    "delta": delta_obj,
                    "finish_reason": None,
                }],
                "created": created,
                "model": model,
                "object": "chat.completion.chunk",
            }
            chunks.append(json.dumps(chunk_start, ensure_ascii=False))

            # 逐个发送tool_calls
            for i, tc in enumerate(tool_calls):
                tc_delta = {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [{
                        "index": i,
                        "id": tc["id"],
                        "type": "function",
                        "function": {
                            "name": tc["function"]["name"],
                            "arguments": tc["function"]["arguments"],
                        },
                    }],
                }
                chunk_tc = {
                    "id": f"chatcmpl-{created}",
                    "choices": [{
                        "index": 0,
                        "delta": tc_delta,
                        "finish_reason": None,
                    }],
                    "created": created,
                    "model": model,
                    "object": "chat.completion.chunk",
                }
                chunks.append(json.dumps(chunk_tc, ensure_ascii=False))

            # finish chunk
            chunk_end = {
                "id": f"chatcmpl-{created}",
                "choices": [{
                    "index": 0,
                    "delta": {},
                    "finish_reason": "tool_calls",
                }],
                "created": created,
                "model": model,
                "object": "chat.completion.chunk",
            }
            chunks.append(json.dumps(chunk_end, ensure_ascii=False))
            chunks.append("[DONE]")
        else:
            # 流式文本 - 逐字发送
            content = fallback_text or "好的。"
            for char in content:
                chunk = {
                    "id": f"chatcmpl-{created}",
                    "choices": [{
                        "index": 0,
                        "delta": {"role": "assistant", "content": char},
                        "finish_reason": None,
                    }],
                    "created": created,
                    "model": model,
                    "object": "chat.completion.chunk",
                }
                chunks.append(json.dumps(chunk, ensure_ascii=False))

            chunk_end = {
                "id": f"chatcmpl-{created}",
                "choices": [{
                    "index": 0,
                    "delta": {},
                    "finish_reason": "stop",
                }],
                "created": created,
                "model": model,
                "object": "chat.completion.chunk",
            }
            chunks.append(json.dumps(chunk_end, ensure_ascii=False))
            chunks.append("[DONE]")

        response_body = "data: " + "\n\ndata: ".join(chunks) + "\n\n"
    else:
        response_body = json.dumps(response_obj, ensure_ascii=False)
    
    return {"response_body": response_body, "match_detail": match_detail}
# ============================================================
# 服务层：FastAPI + 多用户 Token 鉴权 + 管理面板（v2.0 多用户版）
# ============================================================

import os
import sys
import uuid
import secrets
import hashlib
import threading
from pathlib import Path
from datetime import datetime, timedelta
from contextlib import asynccontextmanager

try:
    from fastapi import FastAPI, Request, HTTPException, Depends, status
    from fastapi.responses import (
        HTMLResponse, JSONResponse, PlainTextResponse,
        StreamingResponse, Response
    )
    from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
    from pydantic import BaseModel
except ImportError:
    print("缺少依赖，请先运行: pip install fastapi uvicorn pydantic")
    sys.exit(1)


BASE_DIR = Path(__file__).parent if '__file__' in dir() else Path.cwd()
LOG_DIR = BASE_DIR / "fake_llm_logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
USERS_FILE = BASE_DIR / "users.json"
CONFIG_FILE = BASE_DIR / "config.json"
USAGE_DIR = BASE_DIR / "usage_logs"
USAGE_DIR.mkdir(parents=True, exist_ok=True)


DEFAULT_ADMIN_PASSWORD = "admin123456"  # 首次使用后请在管理面板修改
# 绑定地址与 CORS 来源均可通过环境变量覆盖（公网部署务必收紧）
HOST = os.getenv("BIND_HOST", "0.0.0.0")
PORT = 9998
CORS_ALLOW_ORIGIN = os.getenv("CORS_ALLOW_ORIGIN", "*")


# ============================================================
# 用户管理（JSON文件存储）
# ============================================================

def _hash_token(token: str) -> str:
    """Token 的 SHA256 哈希存储"""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _hash_password(password: str, salt: str = "") -> str:
    """带 salt 的管理员密码哈希。salt 为空时退化为旧方案，兼容历史配置。"""
    return hashlib.sha256((salt + password).encode("utf-8")).hexdigest()


def load_users() -> dict:
    """加载用户列表"""
    if not USERS_FILE.exists():
        return {}
    try:
        with open(USERS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_users(users: dict):
    """保存用户列表"""
    with open(USERS_FILE, "w", encoding="utf-8") as f:
        json.dump(users, f, ensure_ascii=False, indent=2)


def generate_token() -> str:
    """生成 32 字符随机 Token"""
    return "benben_" + secrets.token_hex(16)


def get_user_by_token(token: str) -> dict | None:
    """通过 Token 查找用户"""
    users = load_users()
    token_hash = _hash_token(token)
    for user_id, user_data in users.items():
        if user_data.get("token_hash") == token_hash:
            return {"id": user_id, **user_data}
    return None


def get_user_by_id(user_id: str) -> dict | None:
    """通过 ID 查找用户（不包含token_hash返回前端）"""
    users = load_users()
    if user_id in users:
        u = users[user_id]
        return {
            "id": user_id,
            "name": u.get("name", ""),
            "created_at": u.get("created_at", ""),
            "last_used_at": u.get("last_used_at", ""),
            "call_count": u.get("call_count", 0),
            "today_calls": u.get("today_calls", 0),
            "today_date": u.get("today_date", ""),
            "has_token": bool(u.get("token_hash")),
            "enabled": u.get("enabled", True),
            "remark": u.get("remark", ""),
        }
    return None


def create_user(name: str, remark: str = "") -> dict:
    """创建新用户，返回明文 Token（只返回一次）"""
    users = load_users()
    user_id = f"u_{uuid.uuid4().hex[:10]}"
    token = generate_token()
    now = datetime.now().isoformat(timespec="seconds")
    users[user_id] = {
        "name": name,
        "remark": remark,
        "token_hash": _hash_token(token),
        "created_at": now,
        "last_used_at": "",
        "call_count": 0,
        "today_calls": 0,
        "today_date": datetime.now().strftime("%Y-%m-%d"),
        "enabled": True,
    }
    save_users(users)
    user_info = get_user_by_id(user_id)
    user_info["token"] = token  # 明文token，仅返回一次
    return user_info


def reset_user_token(user_id: str) -> str:
    """重置用户 Token，返回新的明文 Token"""
    users = load_users()
    if user_id not in users:
        raise ValueError("用户不存在")
    token = generate_token()
    users[user_id]["token_hash"] = _hash_token(token)
    save_users(users)
    return token


def delete_user(user_id: str):
    """删除用户"""
    users = load_users()
    if user_id in users:
        del users[user_id]
        save_users(users)


def update_user(user_id: str, **kwargs):
    """更新用户信息"""
    users = load_users()
    if user_id not in users:
        raise ValueError("用户不存在")
    for key in ["name", "remark", "enabled"]:
        if key in kwargs:
            users[user_id][key] = kwargs[key]
    save_users(users)


def increment_call(user_id: str):
    """增加调用计数"""
    users = load_users()
    if user_id not in users:
        return
    today = datetime.now().strftime("%Y-%m-%d")
    if users[user_id].get("today_date", "") != today:
        users[user_id]["today_calls"] = 0
        users[user_id]["today_date"] = today
    users[user_id]["call_count"] = users[user_id].get("call_count", 0) + 1
    users[user_id]["today_calls"] = users[user_id].get("today_calls", 0) + 1
    users[user_id]["last_used_at"] = datetime.now().isoformat(timespec="seconds")
    save_users(users)


# ============================================================
# 管理员密码
# ============================================================

def load_config() -> dict:
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    cfg = {
        "admin_password": _hash_password(DEFAULT_ADMIN_PASSWORD),
        "password_salt": secrets.token_hex(16),
        "admin_password_changed": False,
    }
    save_config(cfg)
    return cfg


def save_config(cfg: dict):
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


def verify_admin_password(password: str) -> bool:
    cfg = load_config()
    stored = cfg.get("admin_password", "")
    if not stored:
        return password == DEFAULT_ADMIN_PASSWORD
    salt = cfg.get("password_salt", "")
    return _hash_password(password, salt) == stored


def change_admin_password(old_pwd: str, new_pwd: str) -> bool:
    if not verify_admin_password(old_pwd):
        return False
    if len(new_pwd) < 8:
        return False
    cfg = load_config()
    if not cfg.get("password_salt"):
        cfg["password_salt"] = secrets.token_hex(16)
    cfg["admin_password"] = _hash_password(new_pwd, cfg["password_salt"])
    cfg["admin_password_changed"] = True
    save_config(cfg)
    return True


# ============================================================
# Pydantic 模型
# ============================================================

class CreateUserRequest(BaseModel):
    name: str
    remark: str = ""


class UpdateUserRequest(BaseModel):
    name: str = ""
    remark: str = ""
    enabled: bool | None = None


class AdminLoginRequest(BaseModel):
    password: str


class ChangeAdminPasswordRequest(BaseModel):
    old_password: str
    new_password: str


class ChatCompletionRequest(BaseModel):
    model: str = "fake-model"
    messages: list = []
    tools: list = []
    stream: bool = False
    temperature: float = 1.0
    max_tokens: int = 4096


# ============================================================
# Admin Session（简单 Token 会话机制）
# ============================================================

ADMIN_SESSIONS = {}  # session_token -> expiry time

def create_admin_session() -> str:
    token = secrets.token_hex(32)
    ADMIN_SESSIONS[token] = datetime.now() + timedelta(hours=24)
    return token


def verify_admin_session(token: str | None) -> bool:
    if not token:
        return False
    if token in ADMIN_SESSIONS:
        if datetime.now() > ADMIN_SESSIONS[token]:
            del ADMIN_SESSIONS[token]
            return False
        return True
    return False


def cleanup_sessions():
    """定期清理过期会话"""
    now = datetime.now()
    expired = [t for t, exp in ADMIN_SESSIONS.items() if now > exp]
    for t in expired:
        del ADMIN_SESSIONS[t]


# ============================================================
# 日志记录（按用户分文件）
# ============================================================

def log_user_request(user_id: str, user_name: str, request_data: dict, response_data: dict | str, match_detail: dict | None = None):
    """记录用户请求（按用户分文件夹），包含详细匹配信息"""
    user_log_dir = USAGE_DIR / user_id
    user_log_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")

    # 提取用户文本
    user_text = ""
    messages = request_data.get("messages", [])
    for msg in reversed(messages):
        if msg.get("role") == "user":
            content = msg.get("content", "")
            if isinstance(content, list):
                for part in content:
                    if isinstance(part, dict) and part.get("type") == "text":
                        user_text += part.get("text", "")
            elif isinstance(content, str):
                user_text = content
            break

    # 提取响应摘要
    response_summary = {}
    if isinstance(response_data, dict):
        choices = response_data.get("choices", [])
        if choices:
            ch = choices[0]
            msg = ch.get("message", {})
            if msg.get("tool_calls"):
                tc = msg["tool_calls"][0]
                response_summary = {
                    "type": "tool_calls",
                    "tool": tc["function"]["name"],
                    "arguments": tc["function"]["arguments"],
                }
            else:
                response_summary = {
                    "type": "text",
                    "content": msg.get("content", ""),
                }
            response_summary["finish_reason"] = ch.get("finish_reason", "")

    # 构建日志条目 - 包含详细匹配信息
    log_entry = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "user_id": user_id,
        "user_name": user_name,
        "user_text": user_text,
        "response": response_summary,
    }

    # 添加匹配详情
    if match_detail:
        log_entry["match_detail"] = match_detail
        
        # 生成可读的状态描述
        if match_detail.get("matched"):
            matcher_name = match_detail.get("matcher_name", "未知")
            log_entry["status_text"] = f"✅ 已识别 - 使用「{matcher_name}」匹配器"
            if match_detail.get("tool_calls"):
                tools_info = ", ".join([tc.get("name", "") for tc in match_detail.get("tool_calls", [])])
                log_entry["detail_text"] = f"📦 工具调用: {tools_info}"
        else:
            log_entry["status_text"] = "❌ 未识别"
            fallback_reason = match_detail.get("fallback_reason", "")
            if fallback_reason:
                log_entry["detail_text"] = f"📝 原因: {fallback_reason}"
            if match_detail.get("tried_matchers"):
                log_entry["detail_text"] = log_entry.get("detail_text", "") + f" | 尝试过: {', '.join(match_detail['tried_matchers'])}"
            
            # 添加可用工具数量信息
            available_count = match_detail.get("available_tools_count", 0)
            log_entry["detail_text"] = log_entry.get("detail_text", "") + f" | 可用工具数: {available_count}"

    # 写入当日日志（JSON Lines 追加）
    today = datetime.now().strftime("%Y%m%d")
    log_file = user_log_dir / f"log_{today}.jsonl"
    with open(log_file, "a", encoding="utf-8") as f:
        f.write(json.dumps(log_entry, ensure_ascii=False) + "\n")


# ============================================================
# FastAPI 应用
# ============================================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    """启动和关闭事件"""
    load_config()
    print(f"\n[启动] Fake LLM 多用户服务 v2.0")
    print(f"[启动] 服务地址: http://{HOST}:{PORT}")
    print(f"[启动] 管理面板: http://{HOST}:{PORT}/admin")
    users = load_users()
    print(f"[启动] 当前用户数: {len(users)}")
    if len(users) == 0:
        # 自动创建默认用户
        default_token = "benben_default_token_123456"
        default_user = {
            "name": "默认用户",
            "token_hash": _hash_token(default_token),
            "created_at": datetime.now().isoformat(),
            "last_used_at": None,
            "call_count": 0,
            "today_calls": 0,
            "today_date": "",
            "enabled": True,
            "remark": "系统自动创建的默认用户",
        }
        users["default"] = default_user
        save_users(users)
        print(f"[提示] 已自动创建默认用户")
        print(f"[提示] 默认 Token: {default_token}")
        print(f"[提示] 请在奔奔助手配置中使用此 Token")
        print(f"[提示] 或登录管理面板创建新用户")
        print(f"[提示] 默认管理员密码: {DEFAULT_ADMIN_PASSWORD}")
    yield
    print(f"\n[关闭] 服务已停止")


app = FastAPI(title="Fake LLM Server - 多用户版", lifespan=lifespan)
security = HTTPBearer(auto_error=False)


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
) -> dict:
    """依赖：根据 Authorization header 获取用户"""
    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": 401, "message": "缺少 Authorization: Bearer <TOKEN> 头"},
        )
    token = credentials.credentials
    user = get_user_by_token(token)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": 401, "message": "无效的 Token"},
        )
    if not user.get("enabled", True):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": 403, "message": "用户已被禁用，请联系管理员"},
        )
    return user


# ============================================================
# 大模型兼容 API
# ============================================================

@app.post("/compatible-mode/v1/chat/completions")
@app.post("/v1/chat/completions")
async def chat_completions(
    request: Request,
    user: dict = Depends(get_current_user),
):
    """
    OpenAI 兼容的 Chat Completions 接口
    两条路径都支持，便于不同AI助手客户端配置
    """
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="无效的 JSON 请求体")

    user_id = user["id"]
    user_name = user.get("name", "")
    increment_call(user_id)

    is_stream = body.get("stream", False)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")

    # 保存请求日志
    try:
        log_filename = f"req_{timestamp}_{user_id}.json"
        save_log(log_filename, {
            "timestamp": datetime.now().isoformat(),
            "user_id": user_id,
            "user_name": user_name,
            "method": "POST",
            "path": str(request.url.path),
            "body": body,
        })
    except Exception:
        pass

    # 构建响应（复用 build_response 函数）
    build_result = build_response(body, is_stream)
    response_body = build_result["response_body"]
    match_detail = build_result.get("match_detail")

    # 保存响应日志
    try:
        resp_filename = f"resp_{timestamp}_{user_id}.json"
        if is_stream:
            save_log(resp_filename, {"stream": True, "raw": response_body[:3000]})
        else:
            try:
                save_log(resp_filename, json.loads(response_body))
            except Exception:
                save_log(resp_filename, {"raw": response_body[:3000]})
    except Exception:
        pass

    # 记录用户日志（用于管理面板展示）- 无论流式还是非流式都记录
    try:
        if is_stream:
            # 流式响应：从响应体解析最后的完整数据
            try:
                # 尝试从流式响应中提取最后一个完整的 JSON
                lines = response_body.strip().split('\n')
                last_data = None
                for line in lines:
                    if line.startswith('data: ') and line.strip() != 'data: [DONE]':
                        data_str = line[6:].strip()
                        if data_str:
                            last_data = json.loads(data_str)
                
                # 用最后一个数据块构建一个简化的响应对象
                if last_data and "choices" in last_data:
                    log_user_request(user_id, user_name, body, last_data, match_detail)
                else:
                    # 如果无法解析，创建一个占位响应
                    placeholder = {
                        "choices": [{
                            "message": {
                                "content": match_detail.get("fallback_reason", "") or "流式响应",
                                "tool_calls": match_detail.get("tool_calls", [])
                            },
                            "finish_reason": "stop"
                        }]
                    }
                    log_user_request(user_id, user_name, body, placeholder, match_detail)
            except Exception as e:
                print(f"[日志] 流式响应日志记录异常: {e}")
        else:
            response_obj = json.loads(response_body)
            log_user_request(user_id, user_name, body, response_obj, match_detail)
    except Exception as e:
        print(f"[日志] 用户日志记录异常: {e}")

    if is_stream:
        def sse_generator():
            yield response_body.encode("utf-8")
        return StreamingResponse(
            sse_generator(),
            media_type="text/event-stream; charset=utf-8",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "Access-Control-Allow-Origin": CORS_ALLOW_ORIGIN,
            }
        )
    else:
        return Response(
            content=response_body,
            media_type="application/json; charset=utf-8",
            headers={"Access-Control-Allow-Origin": CORS_ALLOW_ORIGIN},
        )


@app.get("/v1/models")
@app.get("/compatible-mode/v1/models")
async def list_models(user: dict = Depends(get_current_user)):
    """模型列表接口"""
    return {
        "object": "list",
        "data": [
            {
                "id": "fake-model",
                "object": "model",
                "created": int(time.time()),
                "owned_by": "fake-llm",
            }
        ]
    }


# ============================================================
# 管理面板 API
# ============================================================

def require_admin(request: Request):
    """依赖：管理员权限校验（已禁用认证，直接通过）"""
    return True


@app.post("/api/admin/login")
async def admin_login(req: AdminLoginRequest):
    """管理员登录"""
    if not verify_admin_password(req.password):
        raise HTTPException(status_code=401, detail="密码错误")
    session_token = create_admin_session()
    resp = JSONResponse(content={
        "success": True,
        "message": "登录成功",
        "session_token": session_token,
    })
    resp.set_cookie(
        key="admin_session",
        value=session_token,
        httponly=True,
        max_age=24 * 3600,
        samesite="lax",
    )
    return resp


@app.post("/api/admin/logout")
async def admin_logout(request: Request):
    """管理员登出"""
    token = request.cookies.get("admin_session")
    if token and token in ADMIN_SESSIONS:
        del ADMIN_SESSIONS[token]
    resp = JSONResponse(content={"success": True})
    resp.delete_cookie("admin_session")
    return resp


@app.post("/api/admin/change-password")
async def admin_change_password(
    req: ChangeAdminPasswordRequest,
    is_admin: bool = Depends(require_admin),
):
    """修改管理员密码"""
    if change_admin_password(req.old_password, req.new_password):
        return {"success": True, "message": "密码修改成功"}
    raise HTTPException(status_code=400, detail="旧密码错误，或新密码太短（至少8位）")


@app.get("/api/admin/users")
async def list_users(is_admin: bool = Depends(require_admin)):
    """用户列表"""
    users = load_users()
    result = []
    for uid in users:
        u = get_user_by_id(uid)
        if u:
            result.append(u)
    # 按创建时间倒序
    result.sort(key=lambda x: x.get("created_at", ""), reverse=True)
    return {"success": True, "data": result, "total": len(result)}


@app.post("/api/admin/users")
async def create_user_api(
    req: CreateUserRequest,
    is_admin: bool = Depends(require_admin),
):
    """创建新用户"""
    if not req.name or len(req.name.strip()) < 1:
        raise HTTPException(status_code=400, detail="用户名不能为空")
    user = create_user(req.name.strip(), req.remark.strip())
    return {"success": True, "data": user}


@app.put("/api/admin/users/{user_id}")
async def update_user_api(
    user_id: str,
    req: UpdateUserRequest,
    is_admin: bool = Depends(require_admin),
):
    """更新用户信息"""
    try:
        kwargs = {}
        if req.name:
            kwargs["name"] = req.name.strip()
        if req.remark != "":
            kwargs["remark"] = req.remark.strip()
        if req.enabled is not None:
            kwargs["enabled"] = req.enabled
        update_user(user_id, **kwargs)
        return {"success": True, "data": get_user_by_id(user_id)}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.post("/api/admin/users/{user_id}/reset-token")
async def reset_token_api(
    user_id: str,
    is_admin: bool = Depends(require_admin),
):
    """重置用户 Token"""
    try:
        new_token = reset_user_token(user_id)
        return {"success": True, "data": {"token": new_token}}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.delete("/api/admin/users/{user_id}")
async def delete_user_api(
    user_id: str,
    is_admin: bool = Depends(require_admin),
):
    """删除用户"""
    delete_user(user_id)
    return {"success": True}


@app.get("/api/admin/stats")
async def admin_stats(is_admin: bool = Depends(require_admin)):
    """统计数据"""
    users = load_users()
    total_calls = sum(u.get("call_count", 0) for u in users.values())
    today_total = sum(
        u.get("today_calls", 0)
        for u in users.values()
        if u.get("today_date", "") == datetime.now().strftime("%Y-%m-%d")
    )
    active_users = sum(
        1 for u in users.values()
        if u.get("today_calls", 0) > 0 and u.get("today_date", "") == datetime.now().strftime("%Y-%m-%d")
    )
    return {
        "success": True,
        "data": {
            "total_users": len(users),
            "enabled_users": sum(1 for u in users.values() if u.get("enabled", True)),
            "total_calls": total_calls,
            "today_calls": today_total,
            "today_active_users": active_users,
        }
    }


@app.get("/api/admin/logs/{user_id}")
async def user_logs(
    user_id: str,
    date: str = "",
    is_admin: bool = Depends(require_admin),
):
    """查看指定用户的使用日志"""
    user_log_dir = USAGE_DIR / user_id
    if not user_log_dir.exists():
        return {"success": True, "data": [], "available_dates": []}

    # 可选日期
    available_dates = sorted(
        [f.stem.replace("log_", "") for f in user_log_dir.glob("log_*.jsonl")],
        reverse=True,
    )

    if not date:
        date = available_dates[0] if available_dates else ""
    if not date:
        return {"success": True, "data": [], "available_dates": available_dates}

    log_file = user_log_dir / f"log_{date}.jsonl"
    if not log_file.exists():
        return {"success": True, "data": [], "available_dates": available_dates}

    entries = []
    with open(log_file, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    entries.append(json.loads(line))
                except Exception:
                    pass

    # 倒序显示（最新的在前）
    entries.reverse()
    return {
        "success": True,
        "data": entries[:500],  # 最多返回500条
        "available_dates": available_dates,
    }


@app.post("/api/admin/test-command")
async def test_command(req: ChatCompletionRequest, is_admin: bool = Depends(require_admin)):
    """测试命令匹配 - 直接返回匹配详情，不记录日志"""
    try:
        body = req.model_dump()
        user_text = ""
        messages = body.get("messages", [])
        for msg in reversed(messages):
            if msg.get("role") == "user":
                content = msg.get("content", "")
                if isinstance(content, list):
                    for part in content:
                        if isinstance(part, dict) and part.get("type") == "text":
                            user_text += part.get("text", "")
                elif isinstance(content, str):
                    user_text = content
                break
        
        # 提取可用工具
        tools = body.get("tools", [])
        available_tool_names = set()
        for tool in tools:
            func = tool.get("function", {})
            name = func.get("name")
            if name:
                available_tool_names.add(name)
        
        # 调用 process_request
        result = process_request(user_text, available_tool_names)
        
        return {
            "success": True,
            "data": {
                "user_text": user_text,
                "match_detail": result["match_detail"],
                "tool_calls": result["tool_calls"],
                "fallback_text": result["fallback_text"],
            }
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"测试失败: {e}")


@app.get("/api/service/restart")
async def restart_service(is_admin: bool = Depends(require_admin)):
    """重启服务（通过重新启动 uvicorn 子进程实现）"""
    try:
        threading.Thread(target=_do_restart, daemon=True).start()
        return {"success": True, "message": "重启命令已发送，服务将在3秒后重启..."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"重启失败: {e}")


def _do_restart():
    """在子线程中执行重启（先等待3秒让响应发送出去）"""
    time.sleep(3)
    python = sys.executable
    os.execv(python, [python, os.path.abspath(__file__)] if '__file__' in dir() else [python, "-m", "fake_llm_server"])


# ============================================================
# 通用路由
# ============================================================

@app.get("/")
async def root():
    """根路径 - 服务状态页"""
    users = load_users()
    total_calls = sum(u.get("call_count", 0) for u in users.values())
    today = datetime.now().strftime("%Y-%m-%d")
    today_calls = sum(
        u.get("today_calls", 0)
        for u in users.values()
        if u.get("today_date", "") == today
    )
    return {
        "service": "Fake LLM Server (Free, Multi-User)",
        "version": "2.0.0",
        "status": "running",
        "endpoints": {
            "chat_completions_v1": "/v1/chat/completions",
            "chat_completions_compatible": "/compatible-mode/v1/chat/completions",
            "models_v1": "/v1/models",
            "admin_panel": "/admin",
        },
        "stats": {
            "registered_users": len(users),
            "total_calls": total_calls,
            "today_calls": today_calls,
        },
        "note": "使用管理面板创建用户获取 Token 后，在 Authorization: Bearer <TOKEN> 中携带",
    }



# ============================================================
# 管理面板前端 HTML
# ============================================================

ADMIN_HTML = r'''
<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Fake LLM Server - 管理面板 v2.0</title>
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body {
    font-family: -apple-system, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif;
    background: #f0f4f8;
    color: #1a202c;
    min-height: 100vh;
  }
  .topbar {
    background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
    color: white;
    padding: 16px 32px;
    display: flex;
    justify-content: space-between;
    align-items: center;
    box-shadow: 0 2px 10px rgba(0,0,0,0.1);
  }
  .topbar h1 { font-size: 20px; font-weight: 600; }
  .topbar .actions button {
    background: rgba(255,255,255,0.2);
    color: white;
    border: 1px solid rgba(255,255,255,0.3);
    padding: 8px 16px;
    border-radius: 6px;
    cursor: pointer;
    margin-left: 8px;
    font-size: 13px;
  }
  .topbar .actions button:hover { background: rgba(255,255,255,0.3); }

  /* 登录页 */
  .login-wrap {
    display: flex;
    justify-content: center;
    align-items: center;
    min-height: calc(100vh - 80px);
  }
  .login-box {
    background: white;
    padding: 40px;
    border-radius: 12px;
    box-shadow: 0 10px 40px rgba(0,0,0,0.1);
    width: 400px;
  }
  .login-box h2 { margin-bottom: 24px; color: #2d3748; text-align: center; }
  .login-box label { display: block; margin-bottom: 8px; font-size: 13px; color: #4a5568; font-weight: 500; }
  .login-box input[type="password"], .login-box input[type="text"] {
    width: 100%;
    padding: 10px 14px;
    border: 1px solid #cbd5e0;
    border-radius: 6px;
    font-size: 14px;
    outline: none;
    transition: border 0.2s;
  }
  .login-box input:focus { border-color: #667eea; box-shadow: 0 0 0 3px rgba(102,126,234,0.1); }
  .login-box button {
    width: 100%;
    margin-top: 20px;
    padding: 10px;
    background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
    color: white;
    border: none;
    border-radius: 6px;
    font-size: 14px;
    font-weight: 500;
    cursor: pointer;
  }
  .login-box button:hover { opacity: 0.9; }
  .login-hint { margin-top: 12px; font-size: 12px; color: #718096; text-align: center; }

  /* 主面板 */
  .container { padding: 24px 32px; max-width: 1400px; margin: 0 auto; }
  .stats-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
    gap: 16px;
    margin-bottom: 24px;
  }
  .stat-card {
    background: white;
    padding: 20px;
    border-radius: 10px;
    box-shadow: 0 1px 4px rgba(0,0,0,0.05);
    border-left: 4px solid #667eea;
  }
  .stat-card.green { border-left-color: #48bb78; }
  .stat-card.orange { border-left-color: #ed8936; }
  .stat-card.purple { border-left-color: #9f7aea; }
  .stat-card .label { font-size: 12px; color: #718096; margin-bottom: 6px; }
  .stat-card .value { font-size: 28px; font-weight: 700; color: #2d3748; }

  /* 标签页 */
  .tabs {
    display: flex;
    gap: 4px;
    margin-bottom: 20px;
    background: white;
    padding: 6px;
    border-radius: 10px;
    box-shadow: 0 1px 4px rgba(0,0,0,0.05);
    width: fit-content;
  }
  .tab {
    padding: 10px 20px;
    border-radius: 6px;
    cursor: pointer;
    font-size: 14px;
    font-weight: 500;
    color: #4a5568;
    transition: all 0.2s;
  }
  .tab:hover { background: #f7fafc; }
  .tab.active {
    background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
    color: white;
  }
  .tab-content { display: none; }
  .tab-content.active { display: block; }

  /* 用户表格 */
  .toolbar {
    display: flex;
    gap: 12px;
    margin-bottom: 16px;
    align-items: center;
    justify-content: space-between;
    flex-wrap: wrap;
  }
  .btn {
    padding: 8px 16px;
    border: none;
    border-radius: 6px;
    cursor: pointer;
    font-size: 13px;
    font-weight: 500;
    transition: all 0.2s;
  }
  .btn-primary { background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; }
  .btn-primary:hover { opacity: 0.9; }
  .btn-success { background: #48bb78; color: white; }
  .btn-success:hover { background: #38a169; }
  .btn-danger { background: #f56565; color: white; }
  .btn-danger:hover { background: #e53e3e; }
  .btn-gray { background: #e2e8f0; color: #4a5568; }
  .btn-gray:hover { background: #cbd5e0; }
  .btn-sm { padding: 4px 10px; font-size: 12px; }

  .panel {
    background: white;
    border-radius: 10px;
    padding: 20px;
    box-shadow: 0 1px 4px rgba(0,0,0,0.05);
    margin-bottom: 20px;
  }
  .panel h3 { margin-bottom: 16px; color: #2d3748; font-size: 16px; }

  table { width: 100%; border-collapse: collapse; font-size: 13px; }
  th, td {
    padding: 12px;
    text-align: left;
    border-bottom: 1px solid #edf2f7;
  }
  th {
    background: #f7fafc;
    color: #4a5568;
    font-weight: 600;
    font-size: 12px;
    text-transform: uppercase;
    letter-spacing: 0.5px;
  }
  tr:hover { background: #fafbfc; }
  .tag {
    display: inline-block;
    padding: 3px 8px;
    border-radius: 4px;
    font-size: 11px;
    font-weight: 500;
  }
  .tag-green { background: #c6f6d5; color: #22543d; }
  .tag-red { background: #fed7d7; color: #742a2a; }
  .tag-blue { background: #bee3f8; color: #2a4365; }

  .token-display {
    background: #f7fafc;
    border: 1px solid #e2e8f0;
    padding: 6px 10px;
    border-radius: 6px;
    font-family: Consolas, "Courier New", monospace;
    font-size: 12px;
    color: #4a5568;
    word-break: break-all;
    user-select: all;
  }
  .row-actions { display: flex; gap: 6px; flex-wrap: wrap; }

  /* 日志列表 */
  .log-entry {
    background: #f7fafc;
    border-left: 3px solid #667eea;
    padding: 12px 16px;
    margin-bottom: 10px;
    border-radius: 0 6px 6px 0;
  }
  .log-entry.tool { border-left-color: #9f7aea; }
  .log-entry.unmatched { border-left-color: #f56565; }
  .log-entry.matched { border-left-color: #48bb78; }
  .log-entry .meta { font-size: 11px; color: #718096; margin-bottom: 6px; }
  .log-entry .user-text { font-size: 14px; color: #2d3748; font-weight: 500; margin-bottom: 6px; }
  .log-entry .resp {
    padding: 8px 12px;
    background: #edf2f7;
    border-radius: 4px;
    font-size: 12px;
    color: #4a5568;
    font-family: Consolas, monospace;
  }
  .log-entry .status-badge {
    display: inline-block;
    padding: 3px 10px;
    border-radius: 12px;
    font-size: 12px;
    font-weight: 600;
    margin-right: 8px;
  }
  .status-matched { background: #c6f6d5; color: #22543d; }
  .status-unmatched { background: #fed7d7; color: #742a2a; }
  .log-detail {
    margin-top: 8px;
    padding: 8px 12px;
    background: #ebf8ff;
    border-radius: 4px;
    font-size: 12px;
    color: #2c5282;
    border: 1px solid #bee3f8;
  }
  .log-detail.unmatched {
    background: #fff5f5;
    color: #742a2a;
    border-color: #fed7d7;
  }
  .log-detail .detail-title {
    font-weight: 600;
    margin-bottom: 4px;
  }
  .log-detail .detail-row {
    margin: 3px 0;
    line-height: 1.6;
  }
  .log-detail .detail-label {
    color: #4a5568;
    font-weight: 500;
  }
  .tool-tag {
    display: inline-block;
    background: #9f7aea;
    color: white;
    padding: 2px 8px;
    border-radius: 4px;
    font-size: 11px;
    margin: 2px;
  }
  .date-select {
    padding: 6px 12px;
    border: 1px solid #e2e8f0;
    border-radius: 6px;
    font-size: 13px;
    background: white;
    cursor: pointer;
  }

  /* 模态框 */
  .modal-overlay {
    display: none;
    position: fixed;
    inset: 0;
    background: rgba(0,0,0,0.5);
    z-index: 1000;
    justify-content: center;
    align-items: center;
  }
  .modal-overlay.show { display: flex; }
  .modal {
    background: white;
    padding: 28px;
    border-radius: 12px;
    width: 480px;
    max-width: 90vw;
    max-height: 85vh;
    overflow-y: auto;
  }
  .modal h3 { margin-bottom: 20px; color: #2d3748; }
  .modal .form-group { margin-bottom: 16px; }
  .modal .form-group label {
    display: block;
    margin-bottom: 6px;
    font-size: 13px;
    color: #4a5568;
    font-weight: 500;
  }
  .modal .form-group input, .modal .form-group textarea {
    width: 100%;
    padding: 8px 12px;
    border: 1px solid #cbd5e0;
    border-radius: 6px;
    font-size: 13px;
    outline: none;
    font-family: inherit;
  }
  .modal .form-group input:focus { border-color: #667eea; }
  .modal-actions {
    display: flex;
    gap: 10px;
    justify-content: flex-end;
    margin-top: 20px;
  }

  .alert {
    padding: 12px 16px;
    border-radius: 6px;
    margin-bottom: 16px;
    font-size: 13px;
  }
  .alert-success { background: #c6f6d5; color: #22543d; }
  .alert-error { background: #fed7d7; color: #742a2a; }
  .alert-info { background: #bee3f8; color: #2a4365; }

  .toast {
    position: fixed;
    top: 20px;
    right: 20px;
    padding: 12px 20px;
    background: #2d3748;
    color: white;
    border-radius: 6px;
    z-index: 2000;
    font-size: 13px;
    box-shadow: 0 4px 20px rgba(0,0,0,0.2);
    opacity: 0;
    transform: translateY(-10px);
    transition: all 0.3s;
  }
  .toast.show { opacity: 1; transform: translateY(0); }
  .toast.success { background: #48bb78; }
  .toast.error { background: #f56565; }
  .token-new {
    background: #fffff0;
    border: 2px solid #ecc94b;
    padding: 12px;
    border-radius: 6px;
    margin: 12px 0;
  }
  .token-new strong { color: #d69e2e; }
</style>
</head>
<body>
<div class="topbar">
  <h1>🤖 Fake LLM Server · 管理面板 v2.0</h1>
  <div class="actions" id="topbarActions">
    <button onclick="restartService()">🔄 重启服务</button>
  </div>
</div>

<!-- Toast -->
<div id="toast" class="toast"></div>

<!-- 主面板 -->
<div id="mainView">
<div class="container">
  <!-- 统计卡片 -->
  <div class="stats-grid">
    <div class="stat-card"><div class="label">注册用户</div><div class="value" id="statUsers">0</div></div>
    <div class="stat-card green"><div class="label">启用中</div><div class="value" id="statEnabled">0</div></div>
    <div class="stat-card orange"><div class="label">今日调用</div><div class="value" id="statToday">0</div></div>
    <div class="stat-card purple"><div class="label">总调用次数</div><div class="value" id="statTotal">0</div></div>
  </div>

  <!-- 标签页 -->
  <div class="tabs">
    <div class="tab active" data-tab="users" onclick="switchTab('users')">👥 用户管理</div>
    <div class="tab" data-tab="test" onclick="switchTab('test')">🧪 命令测试</div>
    <div class="tab" data-tab="docs" onclick="switchTab('docs')">📖 使用说明</div>
    <div class="tab" data-tab="logs" onclick="switchTab('logs')">📜 调用日志</div>
  </div>

  <!-- 用户管理 -->
  <div id="tab-users" class="tab-content active">
    <div class="panel">
      <div class="toolbar">
        <h3 style="margin:0;">用户列表</h3>
        <div>
          <button class="btn btn-gray btn-sm" onclick="refreshStats()">🔄 刷新</button>
          <button class="btn btn-primary btn-sm" onclick="openModal('userModal')">➕ 新建用户</button>
        </div>
      </div>
      <div id="alertArea"></div>
      <div style="overflow-x:auto;">
        <table>
          <thead>
            <tr>
              <th>用户名</th>
              <th>状态</th>
              <th>Token</th>
              <th>创建时间</th>
              <th>最近使用</th>
              <th>今日/总调用</th>
              <th>备注</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody id="userTableBody">
            <tr><td colspan="8" style="text-align:center;color:#718096;padding:40px;">加载中...</td></tr>
          </tbody>
        </table>
      </div>
    </div>
  </div>

  <!-- 命令测试 -->
  <div id="tab-test" class="tab-content">
    <div class="panel">
      <h3>🧪 命令测试工具</h3>
      <p style="margin-bottom:16px;font-size:13px;color:#718096;">
        输入命令文本，测试系统是否能正确识别和匹配工具调用。可以选择模拟客户端发送的工具列表。
      </p>
      
      <div style="margin-bottom:16px;">
        <label style="display:block;margin-bottom:6px;font-size:13px;font-weight:500;">命令文本 *</label>
        <textarea id="testInput" rows="3" placeholder="输入要测试的命令，如：打开空调、温度调到24度、去杭州西湖..." style="width:100%;padding:10px 14px;border:1px solid #cbd5e0;border-radius:6px;font-size:14px;outline:none;resize:vertical;"></textarea>
      </div>
      
      <div style="margin-bottom:16px;">
        <label style="display:block;margin-bottom:6px;font-size:13px;font-weight:500;">模拟工具列表（可选，留空表示启用全部工具）</label>
        <textarea id="testTools" rows="3" placeholder="格式：每行一个工具名，如：&#10;set_climate_power&#10;control_climate_temperature" style="width:100%;padding:10px 14px;border:1px solid #cbd5e0;border-radius:6px;font-size:13px;outline:none;resize:vertical;font-family:Consolas,monospace;"></textarea>
        <div style="margin-top:6px;font-size:12px;color:#718096;">💡 留空则自动启用所有工具（模拟真实客户端未发送 tools 的情况）</div>
      </div>
      
      <div style="display:flex;gap:10px;">
        <button class="btn btn-primary" onclick="runTest()">🚀 测试命令</button>
        <button class="btn btn-gray" onclick="clearTest()">🗑 清空结果</button>
      </div>
    </div>
    
    <div id="testResult" style="display:none;">
      <div class="panel">
        <h3>📊 测试结果</h3>
        <div id="testResultContent"></div>
      </div>
    </div>
  </div>

  <!-- 使用说明 -->
  <div id="tab-docs" class="tab-content">
    <div class="panel">
      <h3>📖 快速开始</h3>
      <p style="margin-bottom:16px;font-size:14px;color:#4a5568;">
        本服务为免费大模型替代品，通过关键词匹配返回正确的 tool_calls，无需调用任何付费大模型 API。
      </p>
      <div style="background:#f7fafc;padding:16px;border-radius:8px;margin-bottom:20px;">
        <strong>📌 配置方式（两种路径均可，任选其一）</strong>
        <div style="margin-top:10px;font-family:Consolas,monospace;font-size:13px;line-height:1.8;">
          服务地址: <code>http://你的公网IP或域名:9998/compatible-mode/v1</code><br>
          备用地址: <code>http://你的公网IP或域名:9998/v1</code><br>
          API Key : <code>&lt;用户Token，在上方用户列表中查看&gt;</code><br>
          模型名称: <code>fake-model</code>（任意值均可）
        </div>
      </div>
      <h3 style="margin-top:20px;">🎯 支持的控制指令示例</h3>
      <table style="font-size:13px;">
        <tr><th>分类</th><th>示例指令</th></tr>
        <tr><td>🌡 空调温度</td><td>温度调到24度 · 空调开 · 风量3档 · 主驾25度 · 温度高点</td></tr>
        <tr><td>🚗 车窗</td><td>打开车窗 · 车窗开50% · 左前窗关闭 · 关闭所有车窗</td></tr>
        <tr><td>🌙 天窗/遮阳帘</td><td>打开天窗 · 关闭遮阳帘 · 天窗开一半</td></tr>
        <tr><td>🧭 导航</td><td>去杭州西湖 · 附近有什么加油站 · 回家 · 开始导航</td></tr>
        <tr><td>💡 灯光</td><td>自动大灯打开 · 后雾灯关闭 · 阅读灯设为自动</td></tr>
        <tr><td>💺 座椅</td><td>主驾座椅加热2档 · 副驾通风打开</td></tr>
        <tr><td>🔊 媒体</td><td>播放音乐 · 下一首 · 音量30% · 暂停播放</td></tr>
        <tr><td>📊 状态查询</td><td>当前车速多少 · 胎压多少 · 还能跑多远 · 车门锁了吗</td></tr>
        <tr><td>🔐 车身</td><td>锁车 · 打开后备箱 · 折叠后视镜 · 寻车</td></tr>
      </table>
    </div>
  </div>

  <!-- 调用日志 -->
  <div id="tab-logs" class="tab-content">
    <div class="panel">
      <div class="toolbar">
        <h3 style="margin:0;">查看用户调用日志</h3>
        <div>
          <select class="date-select" id="logUserSelect" onchange="loadLogs()">
            <option value="">-- 选择用户 --</option>
          </select>
          <select class="date-select" id="logDateSelect" onchange="loadLogs()" style="margin-left:8px;">
            <option value="">-- 选择日期 --</option>
          </select>
        </div>
      </div>
      <div id="logList" style="margin-top:12px;">
        <p style="color:#718096;text-align:center;padding:40px;">请先选择用户查看日志</p>
      </div>
    </div>
  </div>
</div>
</div>

<!-- 创建用户模态框 -->
<div id="userModal" class="modal-overlay">
  <div class="modal">
    <h3 id="userModalTitle">新建用户</h3>
    <div id="userModalAlert"></div>
    <div class="form-group">
      <label>用户名 *</label>
      <input type="text" id="uName" placeholder="如：小明的奔奔助手">
    </div>
    <div class="form-group">
      <label>备注</label>
      <textarea id="uRemark" rows="2" placeholder="可选，如：车牌号、使用设备等"></textarea>
    </div>
    <div class="modal-actions">
      <button class="btn btn-gray" onclick="closeModal('userModal')">取消</button>
      <button class="btn btn-primary" onclick="saveUser()">保存</button>
    </div>
  </div>
</div>

<!-- 修改密码模态框 -->
<div id="pwdModal" class="modal-overlay">
  <div class="modal">
    <h3>修改管理员密码</h3>
    <div id="pwdAlert"></div>
    <div class="form-group">
      <label>当前密码</label>
      <input type="password" id="oldPwd">
    </div>
    <div class="form-group">
      <label>新密码（至少6位）</label>
      <input type="password" id="newPwd">
    </div>
    <div class="form-group">
      <label>确认新密码</label>
      <input type="password" id="newPwd2">
    </div>
    <div class="modal-actions">
      <button class="btn btn-gray" onclick="closeModal('pwdModal')">取消</button>
      <button class="btn btn-primary" onclick="savePwd()">确认修改</button>
    </div>
  </div>
</div>

<!-- Token 显示模态框 -->
<div id="tokenModal" class="modal-overlay">
  <div class="modal">
    <h3>⚠️ 请妥善保存 Token（只显示一次）</h3>
    <div class="alert alert-info">
      此 Token 是调用大模型 API 的凭证，泄露会导致被他人盗用。请立即复制保存！
    </div>
    <div class="token-new">
      <strong>Token:</strong>
      <div style="margin-top:8px;" id="newTokenText" class="token-display"></div>
    </div>
    <div class="modal-actions">
      <button class="btn btn-primary" onclick="copyText(document.getElementById('newTokenText').textContent); closeModal('tokenModal')">
        📋 复制 Token 并关闭
      </button>
    </div>
  </div>
</div>

<script>
const BASE = window.location.origin;
let editingUserId = null;

function showToast(msg, type='success') {
  const t = document.getElementById('toast');
  t.textContent = msg;
  t.className = 'toast show ' + type;
  setTimeout(() => t.classList.remove('show'), 2500);
}

function showAlert(containerId, msg, type='info') {
  const el = document.getElementById(containerId);
  el.innerHTML = '<div class="alert alert-' + type + '">' + msg + '</div>';
  setTimeout(() => el.innerHTML = '', 4000);
}

async function api(method, path, body=null) {
  const opts = { method, credentials: 'include', headers: {} };
  if (body) { opts.headers['Content-Type'] = 'application/json'; opts.body = JSON.stringify(body); }
  const resp = await fetch(BASE + path, opts);
  const data = await resp.json().catch(() => ({}));
  if (!resp.ok) throw new Error(data.detail || data.message || ('HTTP ' + resp.status));
  return data;
}

function copyText(text) {
  navigator.clipboard.writeText(text).then(() => showToast('已复制到剪贴板'));
}

function openModal(id) { document.getElementById(id).classList.add('show'); }
function closeModal(id) { document.getElementById(id).classList.remove('show'); }

function switchTab(name) {
  document.querySelectorAll('.tab').forEach(t => t.classList.toggle('active', t.dataset.tab === name));
  document.querySelectorAll('.tab-content').forEach(c => c.classList.toggle('active', c.id === 'tab-' + name));
}

// ---- 登录 ----
async function doLogin() {
  try {
    const pwd = document.getElementById('loginPwd').value;
    const data = await api('POST', '/api/admin/login', { password: pwd });
    if (data.success) {
      document.getElementById('loginView').style.display = 'none';
      document.getElementById('mainView').style.display = 'block';
      document.getElementById('topbarActions').style.display = 'block';
      refreshStats();
      loadUsers();
    }
  } catch(e) {
    showAlert('loginAlert', e.message, 'error');
  }
}

async function logout() {
  try { await api('POST', '/api/admin/logout'); } catch(e){}
  location.reload();
}

// ---- 统计 ----
async function refreshStats() {
  try {
    const data = await api('GET', '/api/admin/stats');
    if (data.success) {
      document.getElementById('statUsers').textContent = data.data.total_users;
      document.getElementById('statEnabled').textContent = data.data.enabled_users;
      document.getElementById('statToday').textContent = data.data.today_calls;
      document.getElementById('statTotal').textContent = data.data.total_calls;
    }
  } catch(e){}
}

// ---- 用户管理 ----
async function loadUsers() {
  const tbody = document.getElementById('userTableBody');
  const userSelect = document.getElementById('logUserSelect');
  try {
    const data = await api('GET', '/api/admin/users');
    const users = data.data || [];
    if (!users.length) {
      tbody.innerHTML = '<tr><td colspan="8" style="text-align:center;color:#718096;padding:40px;">暂无用户，点击右上角「新建用户」开始</td></tr>';
    } else {
      tbody.innerHTML = users.map(u => `
        <tr>
          <td><strong>${escapeHtml(u.name)}</strong></td>
          <td>${u.enabled
            ? '<span class="tag tag-green">启用</span>'
            : '<span class="tag tag-red">禁用</span>'}</td>
          <td>
            ${u.has_token
              ? '<span class="token-display">••••••••••••</span> <button class="btn btn-gray btn-sm" onclick="resetToken(\''+u.id+'\')" title="重置Token">🔑重置</button>'
              : '<span class="tag tag-red">无Token</span>'}
          </td>
          <td><span class="tag tag-blue">${escapeHtml(u.created_at || '-')}</span></td>
          <td>${escapeHtml(u.last_used_at || '未使用')}</td>
          <td><strong>${u.today_calls||0}</strong> / ${u.call_count||0}</td>
          <td>${escapeHtml(u.remark || '-')}</td>
          <td>
            <div class="row-actions">
              <button class="btn btn-gray btn-sm" onclick="editUser('${u.id}')">编辑</button>
              <button class="btn ${u.enabled ? 'btn-danger' : 'btn-success'} btn-sm" onclick="toggleUser('${u.id}', ${!u.enabled})">
                ${u.enabled ? '禁用' : '启用'}
              </button>
              <button class="btn btn-danger btn-sm" onclick="delUser('${u.id}', '${escapeHtml(u.name)}')">删除</button>
            </div>
          </td>
        </tr>
      `).join('');
    }
    // 填充日志选择下拉
    const opts = ['<option value="">-- 选择用户 --</option>'];
    users.forEach(u => opts.push(`<option value="${u.id}">${escapeHtml(u.name)}</option>`));
    userSelect.innerHTML = opts.join('');
  } catch(e) {
    tbody.innerHTML = '<tr><td colspan="8" style="text-align:center;color:#e53e3e;">加载失败: ' + e.message + '</td></tr>';
  }
}

function escapeHtml(s) {
  if (!s) return '';
  return String(s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}

function editUser(uid) {
  editingUserId = uid;
  document.getElementById('userModalTitle').textContent = '编辑用户';
  // 从当前表格提取数据
  const tr = [...document.querySelectorAll('#userTableBody tr')].find(r => r.innerHTML.includes(uid));
  // 简单起见，先清空让用户输入新的
  document.getElementById('uName').value = '';
  document.getElementById('uRemark').value = '';
  document.getElementById('userModalAlert').innerHTML = '';
  openModal('userModal');
}

async function saveUser() {
  const name = document.getElementById('uName').value.trim();
  const remark = document.getElementById('uRemark').value.trim();
  try {
    if (editingUserId) {
      await api('PUT', '/api/admin/users/' + editingUserId, { name: name || undefined, remark: remark !== '' ? remark : undefined });
      showToast('用户已更新');
    } else {
      if (!name) throw new Error('用户名不能为空');
      const data = await api('POST', '/api/admin/users', { name, remark });
      // 新创建用户，弹窗显示 token
      document.getElementById('newTokenText').textContent = data.data.token;
      openModal('tokenModal');
    }
    closeModal('userModal');
    editingUserId = null;
    document.getElementById('uName').value = '';
    document.getElementById('uRemark').value = '';
    refreshStats();
    loadUsers();
  } catch(e) {
    showAlert('userModalAlert', e.message, 'error');
  }
}

async function resetToken(uid) {
  if (!confirm('重置后旧Token立即失效，用户需要重新配置，确认？')) return;
  try {
    const data = await api('POST', '/api/admin/users/' + uid + '/reset-token');
    document.getElementById('newTokenText').textContent = data.data.token;
    openModal('tokenModal');
    loadUsers();
  } catch(e) {
    showToast(e.message, 'error');
  }
}

async function toggleUser(uid, enabled) {
  try {
    await api('PUT', '/api/admin/users/' + uid, { enabled });
    showToast(enabled ? '用户已启用' : '用户已禁用');
    loadUsers();
    refreshStats();
  } catch(e) { showToast(e.message, 'error'); }
}

async function delUser(uid, name) {
  if (!confirm('确认删除用户「' + name + '」？其Token将永久失效！')) return;
  try {
    await api('DELETE', '/api/admin/users/' + uid);
    showToast('用户已删除');
    loadUsers();
    refreshStats();
  } catch(e) { showToast(e.message, 'error'); }
}

// ---- 修改密码 ----
async function savePwd() {
  const oldP = document.getElementById('oldPwd').value;
  const newP = document.getElementById('newPwd').value;
  const newP2 = document.getElementById('newPwd2').value;
  if (newP.length < 6) { showAlert('pwdAlert', '新密码至少6位', 'error'); return; }
  if (newP !== newP2) { showAlert('pwdAlert', '两次输入的新密码不一致', 'error'); return; }
  try {
    await api('POST', '/api/admin/change-password', { old_password: oldP, new_password: newP });
    closeModal('pwdModal');
    document.getElementById('oldPwd').value = '';
    document.getElementById('newPwd').value = '';
    document.getElementById('newPwd2').value = '';
    showToast('管理员密码修改成功');
  } catch(e) {
    showAlert('pwdAlert', e.message, 'error');
  }
}

// ---- 日志 ----
async function loadLogs() {
  const uid = document.getElementById('logUserSelect').value;
  const date = document.getElementById('logDateSelect').value;
  const list = document.getElementById('logList');
  if (!uid) { list.innerHTML = '<p style="color:#718096;text-align:center;padding:40px;">请先选择用户查看日志</p>'; return; }
  list.innerHTML = '<p style="color:#718096;text-align:center;padding:40px;">加载中...</p>';
  try {
    let url = '/api/admin/logs/' + uid;
    if (date) url += '?date=' + date;
    const data = await api('GET', url);
    // 填充日期下拉
    const dateSel = document.getElementById('logDateSelect');
    const opts = ['<option value="">-- 选择日期 --</option>'];
    (data.available_dates || []).forEach(d => {
      opts.push(`<option value="${d}" ${d===date?'selected':''}>${d}</option>`);
    });
    dateSel.innerHTML = opts.join('');
    const entries = data.data || [];
    if (!entries.length) {
      list.innerHTML = '<p style="color:#718096;text-align:center;padding:40px;">该日无调用记录</p>';
      return;
    }
    list.innerHTML = entries.map(e => {
      const r = e.response || {};
      const md = e.match_detail || {};
      let respHtml = '';
      if (r.type === 'tool_calls') {
        respHtml = `<span class="tag tag-blue">🛠 工具调用</span> <strong>${escapeHtml(r.tool)}</strong> 参数: <code>${escapeHtml(r.arguments)}</code>`;
      } else {
        respHtml = `<span class="tag tag-green">💬 文本回复</span> ${escapeHtml(r.content || '')}`;
      }
      
      // 构建详细匹配信息
      let detailHtml = '';
      if (Object.keys(md).length > 0) {
        const isMatched = md.matched;
        const detailClass = isMatched ? '' : ' unmatched';
        let detailContent = '';
        
        if (isMatched) {
          detailContent = `
            <div class="detail-row"><span class="detail-label">匹配器:</span> ${escapeHtml(md.matcher_name || md.matcher_used || '未知')}</div>
            <div class="detail-row"><span class="detail-label">结果:</span> ${escapeHtml(md.match_result || '已匹配')}</div>
          `;
          if (md.tool_calls && md.tool_calls.length > 0) {
            detailContent += '<div class="detail-row"><span class="detail-label">工具调用:</span> ';
            md.tool_calls.forEach(tc => {
              detailContent += `<span class="tool-tag">${escapeHtml(tc.name || '')}</span>`;
              if (tc.arguments) {
                detailContent += `<br><span style="margin-left:20px;color:#718096;font-size:11px;">参数: ${escapeHtml(tc.arguments)}</span>`;
              }
            });
            detailContent += '</div>';
          }
        } else {
          detailContent = `
            <div class="detail-row"><span class="detail-label">状态:</span> ❌ 未识别</div>
          `;
          if (md.fallback_reason) {
            detailContent += `<div class="detail-row"><span class="detail-label">原因:</span> ${escapeHtml(md.fallback_reason)}</div>`;
          }
          if (md.tried_matchers && md.tried_matchers.length > 0) {
            detailContent += `<div class="detail-row"><span class="detail-label">尝试过的匹配器:</span> ${escapeHtml(md.tried_matchers.join(', '))}</div>`;
          }
          if (md.available_tools_count !== undefined) {
            detailContent += `<div class="detail-row"><span class="detail-label">可用工具数:</span> ${md.available_tools_count}</div>`;
          }
        }
        
        detailHtml = `
          <div class="log-detail${detailClass}">
            <div class="detail-title">📊 匹配详情</div>
            ${detailContent}
          </div>
        `;
      }
      
      // 状态徽章
      const statusBadge = e.status_text ? `<span class="status-badge ${md.matched ? 'status-matched' : 'status-unmatched'}">${escapeHtml(e.status_text)}</span>` : '';
      
      // 确定日志条目样式类
      const entryClass = md.matched ? 'matched' : (md.matched === false ? 'unmatched' : (r.type==='tool_calls' ? 'tool' : ''));
      
      return `<div class="log-entry ${entryClass}">
        <div class="meta">
          ${escapeHtml(e.timestamp)} · 
          ${statusBadge}
          finish: ${escapeHtml(r.finish_reason||'')}
        </div>
        <div class="user-text">👤 <span style="color:#718096;font-size:12px;">发送:</span> ${escapeHtml(e.user_text || '')}</div>
        <div class="resp">🤖 <span style="color:#718096;font-size:11px;">返回:</span> ${respHtml}</div>
        ${detailHtml}
      </div>`;
    }).join('');
  } catch(e) {
    list.innerHTML = '<p style="color:#e53e3e;text-align:center;padding:20px;">加载失败: ' + e.message + '</p>';
  }
}

// ---- 命令测试 ----
async function runTest() {
  const input = document.getElementById('testInput').value.trim();
  const toolsText = document.getElementById('testTools').value.trim();
  
  if (!input) {
    showToast('请输入要测试的命令', 'error');
    return;
  }
  
  // 构建请求体
  const requestBody = {
    model: 'fake-model',
    messages: [{ role: 'user', content: input }],
    stream: false
  };
  
  // 如果有指定工具列表
  if (toolsText) {
    const toolNames = toolsText.split('\\n').map(t => t.trim()).filter(t => t);
    requestBody.tools = toolNames.map(name => ({ function: { name: name } }));
  }
  
  const resultDiv = document.getElementById('testResult');
  const contentDiv = document.getElementById('testResultContent');
  resultDiv.style.display = 'block';
  contentDiv.innerHTML = '<p style="color:#718096;text-align:center;padding:20px;">测试中...</p>';
  
  try {
    const data = await api('POST', '/api/admin/test-command', requestBody);
    const result = data.data;
    const md = result.match_detail || {};
    
    let html = '';
    
    // 状态指示
    if (md.matched) {
      html += '<div style="background:#c6f6d5;color:#22543d;padding:10px 14px;border-radius:6px;margin-bottom:12px;font-weight:600;">';
      html += '✅ 命令已识别';
      if (md.matcher_name) html += ` - 使用「${escapeHtml(md.matcher_name)}」匹配器`;
      html += '</div>';
    } else {
      html += '<div style="background:#fed7d7;color:#742a2a;padding:10px 14px;border-radius:6px;margin-bottom:12px;font-weight:600;">';
      html += '❌ 命令未识别';
      html += '</div>';
    }
    
    // 输入输出展示
    html += '<div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:12px;">';
    html += '<div style="background:#f7fafc;padding:12px;border-radius:6px;"><strong style="color:#4a5568;font-size:12px;">📥 输入命令</strong>';
    html += `<div style="margin-top:6px;font-size:14px;color:#2d3748;">${escapeHtml(result.user_text)}</div></div>`;
    
    if (md.matched) {
      html += '<div style="background:#ebf8ff;padding:12px;border-radius:6px;"><strong style="color:#2c5282;font-size:12px;">📤 识别结果</strong>';
      if (result.tool_calls && result.tool_calls.length > 0) {
        result.tool_calls.forEach(tc => {
          html += `<div style="margin-top:6px;"><span class="tool-tag">${escapeHtml(tc.function.name)}</span></div>`;
          html += `<div style="font-size:11px;color:#718096;margin-top:2px;">参数: ${escapeHtml(tc.function.arguments)}</div>`;
        });
      } else if (result.fallback_text) {
        html += `<div style="margin-top:6px;font-size:13px;color:#4a5568;">${escapeHtml(result.fallback_text)}</div>`;
      }
      html += '</div>';
    } else {
      html += '<div style="background:#fff5f5;padding:12px;border-radius:6px;"><strong style="color:#742a2a;font-size:12px;">📤 返回内容</strong>';
      html += `<div style="margin-top:6px;font-size:13px;color:#4a5568;">${escapeHtml(result.fallback_text || '无')}</div>`;
      html += '</div>';
    }
    html += '</div>';
    
    // 详细匹配信息
    html += '<div style="background:#f7fafc;padding:12px;border-radius:6px;">';
    html += '<strong style="color:#4a5568;font-size:12px;">📊 匹配详情</strong>';
    html += '<div style="margin-top:8px;font-size:13px;">';
    html += `<div style="margin:4px 0;"><span style="color:#718096;">匹配状态:</span> ${md.matched ? '✅ 已匹配' : '❌ 未匹配'}</div>`;
    
    if (md.matcher_name) {
      html += `<div style="margin:4px 0;"><span style="color:#718096;">匹配器:</span> ${escapeHtml(md.matcher_name)}</div>`;
    }
    if (md.match_result) {
      html += `<div style="margin:4px 0;"><span style="color:#718096;">结果类型:</span> ${escapeHtml(md.match_result)}</div>`;
    }
    if (md.fallback_reason) {
      html += `<div style="margin:4px 0;"><span style="color:#718096;">未匹配原因:</span> ${escapeHtml(md.fallback_reason)}</div>`;
    }
    if (md.available_tools_count !== undefined) {
      html += `<div style="margin:4px 0;"><span style="color:#718096;">可用工具数:</span> ${md.available_tools_count}</div>`;
    }
    if (md.tried_matchers && md.tried_matchers.length > 0) {
      html += `<div style="margin:4px 0;"><span style="color:#718096;">尝试过的匹配器:</span> ${escapeHtml(md.tried_matchers.join(', '))}</div>`;
    }
    html += '</div>';
    html += '</div>';
    
    contentDiv.innerHTML = html;
  } catch(e) {
    contentDiv.innerHTML = `<div style="background:#fed7d7;color:#742a2a;padding:12px;border-radius:6px;">测试失败: ${escapeHtml(e.message)}</div>`;
  }
}

function clearTest() {
  document.getElementById('testInput').value = '';
  document.getElementById('testTools').value = '';
  document.getElementById('testResult').style.display = 'none';
}

// ---- 重启服务 ----
async function restartService() {
  if (!confirm('确认立即重启服务？所有连接将断开，约需5秒恢复。')) return;
  try {
    showToast('重启命令已发送，5秒后自动刷新页面...');
    await api('GET', '/api/service/restart');
    setTimeout(() => location.reload(), 5000);
  } catch(e) { showToast(e.message, 'error'); }
}

// ---- 初始化：直接加载主面板 ----
(async function init(){
  refreshStats();
  loadUsers();
})();

// ESC 关闭模态框
document.addEventListener('keydown', e => {
  if (e.key === 'Escape') document.querySelectorAll('.modal-overlay.show').forEach(m => m.classList.remove('show'));
});
// 点击遮罩关闭
document.querySelectorAll('.modal-overlay').forEach(m => m.addEventListener('click', e => {
  if (e.target === m) m.classList.remove('show');
}));
</script>
</body>
</html>
'''


@app.get("/admin")
@app.get("/admin/")
async def admin_panel():
    """管理面板前端页面"""
    return HTMLResponse(content=ADMIN_HTML)


@app.get("/admin/{rest:path}")
async def admin_panel_sub(rest: str):
    """管理面板子路径也返回同一个 SPA"""
    return HTMLResponse(content=ADMIN_HTML)


# ============================================================
# 通用路由（放最后，通配符匹配
# ============================================================

@app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "OPTIONS", "PATCH"])
async def catch_all(request: Request, path: str):
    """CORS 预检和 404"""
    if request.method == "OPTIONS":
        headers = {
            "Access-Control-Allow-Origin": CORS_ALLOW_ORIGIN,
            "Access-Control-Allow-Methods": "GET, POST, PUT, DELETE, OPTIONS, PATCH",
            "Access-Control-Allow-Headers": "Content-Type, Authorization",
        }
        return Response(status_code=200, headers=headers)
    raise HTTPException(status_code=404, detail=f"路径不存在: /{path}")


# ============================================================
# 启动入口
# ============================================================

def serve():
    """启动 uvicorn 服务"""
    import uvicorn
    print(f"""
╔══════════════════════════════════════════════════════════════════════╗
║     车载AI助手"奔奔" - 免费假大模型服务 v2.0 (多用户版)            ║
╠══════════════════════════════════════════════════════════════════════╣
║  兼容模式: http://公网IP或域名:{PORT}/compatible-mode/v1           ║
║  V1模式  : http://公网IP或域名:{PORT}/v1                           ║
║  管理面板: http://公网IP或域名:{PORT}/admin                         ║
║  日志目录: {str(LOG_DIR)}                                                 ║
║                                                                      ║
║  特点:                                                               ║
║  ✅ 完全免费 - 不调用任何大模型API                                 ║
║  ✅ 多用户支持 - 每个用户独立 Token, 独立统计                      ║
║  ✅ 管理面板 - 可视化增删用户、重置Token、查看日志、重启服务       ║
║  ✅ 关键词匹配 - 支持52个车辆控制工具                             ║
║  ✅ 支持流式和非流式响应                                           ║
║                                                                      ║
║  首次使用步骤:                                                       ║
║  1. 浏览器访问  http://你的IP:9998/admin                           ║
║  2. 默认密码: admin123456（登录后立即修改！）                      ║
║  3. 在「用户管理」中创建用户 → 复制 Token                          ║
║  4. 在奔奔助手中配置:                                               ║
║     • 服务地址: http://你的IP:9998/compatible-mode/v1             ║
║     • API Key : <刚才复制的Token>                                  ║
║     • 模型名称: 任意                                               ║
║                                                                      ║
║  按 Ctrl+C 停止服务                                                 ║
╚══════════════════════════════════════════════════════════════════════╝
""")
    uvicorn.run(app, host=HOST, port=PORT, log_level="info")


def main():
    try:
        serve()
    except KeyboardInterrupt:
        print("\n[停止] 服务已关闭")
    except ImportError as e:
        if "uvicorn" in str(e):
            print("\n[错误] 缺少 uvicorn，请运行: pip install uvicorn fastapi pydantic")
        else:
            raise


if __name__ == "__main__":
    main()
