import rclpy
from rclpy.node import Node
from std_msgs.msg import String
from sensor_msgs.msg import Joy
import json  # jsonを使うため
from concurrent.futures import ThreadPoolExecutor  # threadPoolExecutor
import playsound  # バッテリー低電圧保護ブザー用
import subprocess  # SSID取得用
import time
import ipget  # IPアドレス取得用
import socket  # UDP通信用
import math
import copy  # 辞書型をコピーする用
reception_json = {
    "raw_angle": 0,
    "servo_tmp": 999,
    "servo_cur": 999,
    "servo_deg": 999,
    "battery_voltage": 0,
    "wifi_signal_strength": 0
}

# ESP32のIPアドレスとポート番号
# esp32_ip = "192.168.211.78"
# esp32_ip = "192.168.211.241"
# esp32_ip = "192.168.28.241"
# esp32_ip = "192.168.107.241"
esp32_ip = "192.168.107.78"
esp32_port = 12345

# UDPソケットの作成
udp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

sp_udp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sp_udp_socket.bind(('127.0.0.1', 5003))  # 本当は5002
sp_udp_socket.settimeout(1.0)  # タイムアウトを1秒に設定

local_udp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
local_udp_socket.bind(('0.0.0.0', 12346))
local_udp_socket.settimeout(1.0)  # タイムアウトを1秒に設定

try:
    result = subprocess.check_output(
        ['iwgetid', '-r'], universal_newlines=True)
    wifi_ssid = result.strip()
    # wifi_ssid= bytes(result.strip(), 'utf-8').decode('unicode-escape')
except subprocess.CalledProcessError:
    wifi_ssid = "エラー"


def main():
    with ThreadPoolExecutor(max_workers=4) as executor:
        # executor.submit(sp_udp_reception)
        executor.submit(esp32_udp_reception)
        # executor.submit(battery_alert)
        future = executor.submit(ros)
        future.result()         # 全てのタスクが終了するまで待つ


def sp_udp_reception():
    global sp_udp_socket
    global reception_json
    while True:
        try:
            message, cli_addr = sp_udp_socket.recvfrom(1024)
            # print(f"Received: {message.decode('utf-8')}", flush=True)
            reception_json_temp = json.loads(message.decode('utf-8'))
            reception_json.update(reception_json_temp)
        except Exception as e:
            print(
                f"\n\n\n\n\n\n\n    スマホ からの受信に失敗: {e}\n\n\n\n\n\n\n", flush=True)


def esp32_udp_reception():
    global local_udp_socket
    global reception_json
    while True:
        try:
            # データを受信
            data, addr = local_udp_socket.recvfrom(1024)
            print(f"                    {data.decode('utf-8')}", flush=True)
            reception_json_temp = json.loads(data.decode('utf-8'))
            reception_json.update(reception_json_temp)
        except Exception as e:
            print(
                f"\n\n\n\n\n\n\n    ESP32 からの受信に失敗: {e}\n\n\n\n\n\n\n", flush=True)
            reception_json["battery_voltage"] = 0


def battery_alert():
    global reception_json
    temp = 0
    while True:
        if reception_json["battery_voltage"] < 10 and reception_json["battery_voltage"] > 5:
            temp += 1
            if temp > 3:
                playsound.playsound("battery_alert.mp3")
        else:
            temp = 0
        time.sleep(0.2)  # 無駄にCPUを使わないようにする


def ros(args=None):
    rclpy.init(args=args)

    minimal_subscriber = MinimalSubscriber()

    rclpy.spin(minimal_subscriber)

    minimal_subscriber.destroy_node()
    rclpy.shutdown()


class MinimalSubscriber(Node):
    state = 0
    motor_speed = [0, 0, 0, 0, 0]
    servo_angle = 0

    turn_P_gain = 4  # 旋回中に角度センサーにかけられるPゲイン
    angle_adjust = 0
    current_angle = 0

    joy_now = {}
    joy_past = {}

    start_time = 0

    def __init__(self):
        global reception_json
        print("Subscriber", flush=True)
        super().__init__('command_subscriber')
        self.publisher_ESP32_to_Webserver = self.create_publisher(
            String, 'ESP32_to_Webserver', 10)
        self.subscription = self.create_subscription(
            Joy,
            "joy0",
            self.joy0_listener_callback,
            10)
        self.subscription = self.create_subscription(
            Joy,
            "joy1",
            self.joy1_listener_callback,
            10)
        self.subscription  # prevent unused variable warning

        self.timer_0001 = self.create_timer(0.01, self.timer_callback_001)
        self.timer_0016 = self.create_timer(0.033, self.timer_callback_0033)

    def timer_callback_0033(self):
        global wifi_ssid, esp32_ip

        msg = String()
        send_json = {
            "state": self.state,
            "ubuntu_ssid": wifi_ssid,
            "ubuntu_ip": ipget.ipget().ipaddr("wlp2s0"),
            "esp32_ip": esp32_ip,
            "battery_voltage": reception_json["battery_voltage"],
            # "battery_voltage": 6,
            "wifi_signal_strength": reception_json["wifi_signal_strength"],
            "motor_speed": [int(speed) for speed in self.motor_speed],
            "servo_angle": int(self.servo_angle),
            "servo_tmp": reception_json["servo_tmp"],
            "servo_cur": reception_json["servo_cur"],
            "servo_deg": reception_json["servo_deg"],
            "angle_value": self.current_angle,
            "start_time": self.start_time,
            "joy": self.joy_now
        }
        msg.data = json.dumps(send_json)
        self.publisher_ESP32_to_Webserver.publish(msg)

    def timer_callback_001(self):
        global reception_json
        try:
            self.current_angle = reception_json["raw_angle"] + \
                self.angle_adjust
            if self.current_angle < 0:
                self.current_angle = 360 + self.current_angle

            # print(reception_json["raw_angle"],self.angle_adjust,self.current_angle,flush=True)

            if self.state == 0:
                # 走行補助がオフなら
                turn_minus1to1 = 0
            elif self.state == 1:
                turn_minus1to1 = self.turn(0)
            elif self.state == 2:
                turn_minus1to1 = self.turn(90)
            elif self.state == 3:
                turn_minus1to1 = self.turn(180)
            elif self.state == 4:
                turn_minus1to1 = self.turn(270)

            # 手動旋回と自動旋回を合わせる
            turn_minus1to1 += self.joy_now["joy0"]["axes"][0]

            self.motor_speed[:4] = [
                turn_minus1to1 * 256 * -1,
                turn_minus1to1 * 256,
                turn_minus1to1 * 256 * -1,
                turn_minus1to1 * 256]

            normalized_angle = (
                1 - (math.atan2(self.joy_now["joy0"]["axes"][2], self.joy_now["joy0"]["axes"][3])) / (2 * math.pi)) % 1
            distance = math.sqrt(
                self.joy_now["joy0"]["axes"][2]**2 + self.joy_now["joy0"]["axes"][3]**2)
            if distance > 1:
                distance = 1

            # 制御関数の呼び出し
            front_left, front_right, rear_left, rear_right = self.control_mecanum_wheels(
                normalized_angle, distance)  # 0から1の範囲で指定（北を0、南を0.5として時計回りに）

            # 旋回と合わせる
            self.motor_speed[:4] = [speed + value for speed, value in zip(
                self.motor_speed, [front_left, front_right, rear_left, rear_right])]

            # 255を超えた場合、比率を保ったまま255以下にする
            max_motor_speed = max(map(abs, self.motor_speed[:4]))
            if max_motor_speed > 255:
                self.motor_speed = [int(speed * 255 / max_motor_speed)
                                    for speed in self.motor_speed[:4]]

            print(self.state,
                  *[int(speed) for speed in self.motor_speed],
                  int(self.servo_angle),
                  flush=True)

            send_ESP32_data = {
                "motor1": int(self.motor_speed[0]),
                "motor2": int(self.motor_speed[1]),
                "motor3": int(self.motor_speed[2]),
                "motor4": int(self.motor_speed[3]),
                "motor5": int(self.motor_speed[4]),
                "servo": int(self.servo_angle),
            }
            json_str = json.dumps(send_ESP32_data) + "\n"
            # print(f"Sent {esp32_ip}:{esp32_port} {json_str}",flush=True)
            try:
                udp_socket.sendto(json_str.encode(), (esp32_ip, esp32_port))
            except Exception as e:
                print(
                    f"\n\n\n\n\n\n\n    ESP32 への送信に失敗: {e}\n\n\n\n\n\n\n", flush=True)
        except KeyError as e:
            print(f"コントローラー の読み取りに失敗: {e}", flush=True)

    def joy0_listener_callback(self, joy):
        global reception_json

        self.joy_now.update({
            "joy0":
            {"axes": list(joy.axes),
             "buttons": list(joy.buttons)}
        })
        self.joy_past.setdefault(
            "joy0",
            {"axes": [0] * len(joy.axes), "buttons": [0] * len(joy.buttons)}
        )

        # self.motor5_speed = int(self.joy_now["joy0"]["axes"][1]*256)

        if self.joy_past["joy0"]["buttons"][2] == 0 and self.joy_now["joy0"]["buttons"][2] == 1:  # Xボタン
            # 0°に旋回
            self.state = 1
        if self.joy_past["joy0"]["buttons"][1] == 0 and self.joy_now["joy0"]["buttons"][1] == 1:  # Aボタン
            # 90°に旋回
            self.state = 2
        if self.joy_past["joy0"]["buttons"][0] == 0 and self.joy_now["joy0"]["buttons"][0] == 1:  # Bボタン
            # 180°に旋回
            self.state = 3
        if self.joy_past["joy0"]["buttons"][3] == 0 and self.joy_now["joy0"]["buttons"][3] == 1:  # Yボタン
            # 270°に旋回
            self.state = 4
        if self.joy_past["joy0"]["buttons"][13] == 0 and self.joy_now["joy0"]["buttons"][13] == 1:  # upボタン
            # 排出蓋を閉じる
            self.servo_angle = -135
        if self.joy_past["joy0"]["buttons"][14] == 0 and self.joy_now["joy0"]["buttons"][14] == 1:  # downボタン
            # 排出蓋を開く
            self.servo_angle = 135

        # LボタンとRボタン同時押し
        if self.joy_past["joy0"]["buttons"][4] == 0 and self.joy_now["joy0"]["buttons"][5] == 1:
            # 角度リセット
            if reception_json["raw_angle"] < 0:
                # マイナスのとき
                self.angle_adjust = - 180 - reception_json["raw_angle"]
            else:
                self.angle_adjust = -1 * reception_json["raw_angle"]

        if self.joy_past["joy0"]["buttons"][10] == 0 and self.joy_now["joy0"]["buttons"][10] == 1:  # homeボタン
            # タイマースタート
            self.start_time = time.time()

        if self.joy_now["joy0"]["buttons"][6] == 1 or self.joy_now["joy0"]["buttons"][7] == 1:  # ZRボタンまたはZLボタン
            # 走行補助強制停止
            self.state = 0
            self.motor_speed = [0] * 5

        self.joy_past["joy0"] = self.joy_now["joy0"]

    def joy1_listener_callback(self, joy):
        self.joy_now.update({
            "joy1":
            {"axes": list(joy.axes),
             "buttons": list(joy.buttons)}
        })
        self.joy_past.setdefault(
            "joy1",
            {"axes": [0] * len(joy.axes), "buttons": [0] * len(joy.buttons)}
        )

        # 回収機構のモーター
        self.motor_speed[4] = int(self.joy_now["joy1"]["axes"][1] * 256)

        if self.joy_now["joy1"]["buttons"][6] == 1 or self.joy_now["joy1"]["buttons"][7]:
            # 走行補助強制停止
            self.state = 0
            self.motor_speed = [0] * 5

        self.joy_past["joy1"] = self.joy_now["joy1"]

    def control_mecanum_wheels(self, direction, speed):
        # ラジアンに変換
        angle = direction * 2.0 * math.pi

        # 回転数を255から-255の範囲に変換
        front_left = math.sin(angle + math.pi / 4.0) * 255
        front_right = math.cos(angle + math.pi / 4.0) * 255
        rear_left = math.cos(angle + math.pi / 4.0) * 255
        rear_right = math.sin(angle + math.pi / 4.0) * 255
        adjust = 255 / max([abs(front_left), abs(front_right),
                           abs(rear_left), abs(rear_right)])
        front_left = int(front_left * adjust * speed)
        front_right = int(front_right * adjust * speed)
        rear_left = int(rear_left * adjust * speed)
        rear_right = int(rear_right * adjust * speed)

        return front_left, front_right, rear_left, rear_right

    def turn(self, target_angle):
        target_plus_180 = target_angle + 180
        if target_plus_180 > 360:
            target_plus_180 = 360 - target_angle
        angle_difference = abs(self.current_angle - target_angle)
        if angle_difference > 180:
            angle_difference = 360 - abs(self.current_angle-target_angle)
        if target_angle > 180:
            if target_plus_180 <= self.current_angle <= target_angle:
                angle_difference = angle_difference*-1
        else:
            if target_angle <= self.current_angle <= target_plus_180:
                pass
            else:
                angle_difference = angle_difference*-1

        if abs(angle_difference) < 3:
            # 止まる
            temp = 0
            self.state = 0
        else:
            temp = angle_difference/360 * self.turn_P_gain
            if temp > 1:
                temp = 1
            if temp < -1:
                temp = -1
        return temp


if __name__ == '__main__':
    main()
