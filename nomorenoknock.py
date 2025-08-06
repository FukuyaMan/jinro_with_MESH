import asyncio
from datetime import datetime
from bleak import BleakClient, discover
from struct import pack
import csv
import os

# 定数
SN_TH = "MESH-100TH1026989"
SN_MD = "MESH-100MD1049341"
SN_AC = "MESH-100AC1029724"
CORE_INDICATE_UUID = '72c90005-57a9-4d40-b746-534e22ec9f9e'
CORE_NOTIFY_UUID = '72c90003-57a9-4d40-b746-534e22ec9f9e'
CORE_WRITE_UUID = '72c90004-57a9-4d40-b746-534e22ec9f9e'
CSV_FILE_NAME = 'room_status.csv'
CSV_HEADERS = ["部屋ID", "空室状況", "温度", "湿度", "入室開始時刻"]

# 部屋の状態
room_status = {
    'id': 'Room-A',
    'occupancy': '空室',
    'temperature': 'N/A',
    'humidity': 'N/A',
    'entry_start_time': ''
}

# 状態フラグ
motion_detected = False
away_mode = False

def update_csv():
    # CSVファイル更新
    with open(CSV_FILE_NAME, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow(CSV_HEADERS)
        writer.writerow([
            room_status['id'],
            room_status['occupancy'],
            room_status['temperature'],
            room_status['humidity'],
            room_status['entry_start_time']
        ])
    print(f"CSVファイル更新: {room_status}")

def checksum(data):
    # チェックサム計算
    return sum(data) & 0xFF

def parse_th_data(data):
    # 温湿度データ解析
    temp_val = int.from_bytes(data[4:6], byteorder='little', signed=True)
    hum_val = int.from_bytes(data[6:8], byteorder='little', signed=True)
    temp_c = (temp_val - 65536) / 10.0 if 65436 <= temp_val <= 65535 else temp_val / 10.0
    hum_p = hum_val
    return round(temp_c, 1), hum_p

def on_receive_th_notify(sender, data: bytearray):
    # 温湿度ブロックからの通知
    global room_status
    if len(data) >= 8 and data[0] == 0x01 and data[1] == 0x00:
        temp, hum = parse_th_data(data)
        room_status['temperature'] = f"{temp} ℃"
        room_status['humidity'] = f"{hum} %"

def on_receive_md_notify(sender, data: bytearray):
    # 人感ブロックからの通知
    global motion_detected
    if len(data) >= 4 and data[0] == 0x01 and data[1] == 0x00:
        motion_detected = data[3] == 0x01

def on_receive_ac_notify(sender, data: bytearray):
    # 動きブロックからの通知
    global away_mode
    if len(data) >= 3 and data[0] == 0x01 and data[1] == 0x03:
        orientation = data[2]
        away_mode = orientation == 0x04

async def connect_and_setup(serial_number, notify_handler=None):
    # ブロックに接続して設定
    print(f"{serial_number}に接続中...")
    device = await find_device_by_serial(serial_number)
    if not device:
        print(f"デバイスが見つかりません: {serial_number}")
        return None
    try:
        client = BleakClient(device, timeout=None)
        await client.connect()
        print(f"{serial_number}に接続完了")
        # 機能有効化コマンドを送信
        await client.write_gatt_char(CORE_WRITE_UUID, pack('<BBBB', 0x00, 0x02, 0x01, 0x03), response=True)
        print(f"{serial_number}の機能を有効化しました")
        if notify_handler:
            await client.start_notify(CORE_NOTIFY_UUID, notify_handler)
            print(f"{serial_number}の通知を開始しました")
        return client
    except Exception as e:
        print(f"{serial_number}への接続エラー: {e}")
        return None

async def find_device_by_serial(serial_number):
    # シリアルナンバーでデバイスを検索
    devices = await discover()
    return next((d for d in devices if d.name and d.name.startswith(serial_number)), None)

async def setup_all_blocks():
    # 全ブロックの接続と初期設定
    global th_client, md_client, ac_client
    print("--- MESHブロックのセットアップを開始します ---")
    th_client = await connect_and_setup(SN_TH, on_receive_th_notify)
    md_client = await connect_and_setup(SN_MD, on_receive_md_notify)
    if md_client:
        # 人感ブロックの通知モードを設定
        md_mode_setting = pack('<BBBB', 0x01, 0x00, 0x00, 0x20)
        await md_client.write_gatt_char(CORE_WRITE_UUID, md_mode_setting + pack('B', checksum(md_mode_setting)), response=True)
        print("人感ブロックを定期通知モードに設定しました")
    ac_client = await connect_and_setup(SN_AC, on_receive_ac_notify)
    if ac_client:
        # 動きブロックのオリエンテーションイベントを設定
        ac_mode_setting = pack('<BBB', 0x01, 0x03, 0x00)
        await ac_client.write_gatt_char(CORE_WRITE_UUID, ac_mode_setting + pack('B', checksum(ac_mode_setting)), response=True)
        print("動きブロックを向き変化通知モードに設定しました")
    if not all([th_client, md_client, ac_client]):
        print("エラー: 全てのブロックに接続できませんでした。終了します。")
        return False
    print("--- セットアップ完了 ---")
    return True

async def main_loop():
    # メインループ
    global room_status, motion_detected, away_mode
    if not await setup_all_blocks():
        return
    if th_client:
        await th_client.write_gatt_char(CORE_WRITE_UUID, pack('<BBBB', 0x00, 0x03, 0x00, 0x03), response=True)
        print("温湿度ブロックに初期データ要求を送信しました")
    if md_client:
        # 人感ブロックに現在の状態を1回通知要求
        md_onetime_request = pack('<BBBBHH', 0x01, 0x00, 0x01, 0x10, 500, 500)
        await md_client.write_gatt_char(CORE_WRITE_UUID, md_onetime_request + pack('B', checksum(md_onetime_request)), response=True)
        print("人感ブロックに初期状態要求を送信しました")
    if not os.path.exists(CSV_FILE_NAME):
        update_csv()
    while True:
        try:
            current_occupancy = room_status['occupancy']
            if away_mode:
                if current_occupancy != '退席中':
                    room_status['occupancy'] = '退席中'
                print("退席モード中です。空室への変更はスキップされます。")
            else:
                if motion_detected:
                    if current_occupancy == '空室':
                        room_status['entry_start_time'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                    room_status['occupancy'] = '使用中'
                else:
                    if current_occupancy != '空室':
                        room_status['occupancy'] = '空室'
                        room_status['entry_start_time'] = ''
            update_csv()
        except Exception as e:
            print(f"メインループでエラーが発生しました: {e}")
            break
        await asyncio.sleep(15)

if __name__ == '__main__':
    loop = asyncio.get_event_loop()
    try:
        loop.run_until_complete(main_loop())
    except KeyboardInterrupt:
        print("ユーザーによってプログラムが停止されました。")
    finally:
        print("MESHブロックから切断します...")
        if th_client and th_client.is_connected:
            loop.run_until_complete(th_client.disconnect())
        if md_client and md_client.is_connected:
            loop.run_until_complete(md_client.disconnect())
        if ac_client and ac_client.is_connected:
            loop.run_until_complete(ac_client.disconnect())