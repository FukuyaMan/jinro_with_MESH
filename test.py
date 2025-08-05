import asyncio
import time
from bleak import BleakClient, BleakScanner
from collections import Counter

# MESHブロックの共通サービスUUIDと特性UUID
# 全てのMESHブロックが持つ共通サービスUUID
MESH_SERVICE_UUID = "72c90001-57a9-4d40-b746-534e22ec9f9e"

# このサービスに属する特性UUID群
# コマンド送信 (Write Without Response)
COMMAND_CHAR_UUID = "72c90002-57a9-4d40-b746-534e22ec9f9e"
# イベント通知 (Notify)
NOTIFICATION_CHAR_UUID = "72c90003-57a9-4d40-b746-534e22ec9f9e"
# 状態設定など (Write)
STATE_COMMAND_CHAR_UUID = "72c90004-57a9-4d40-b746-534e22ec9f9e"
# 状態取得 (Indicate, ACK必要)
STATE_INDICATION_CHAR_UUID = "72c90005-57a9-4d40-b746-534e22ec9f9e"

# コマンドIDと通知ID
# LED制御コマンド
CMD_ID_LED_CONTROL = 0x01
# PWM出力制御コマンド (ブザー)
CMD_ID_PWM_CONTROL = 0x05
# ボタンイベント通知ID
NOTIF_ID_BUTTON_EVENT = 0x01
# 動きセンサーイベント通知ID (向き)
NOTIF_ID_MOTION_ORIENTATION = 0x03

# 動きブロックの向きの値 (仕様書に基づく)
ORIENTATION_LEFT = 0x01
ORIENTATION_UP = 0x05
ORIENTATION_RIGHT = 0x06
ORIENTATION_FRONT = 0x03 # 表
ORIENTATION_BACK = 0x04 # 裏

# 色定義 (RGB値のタプル)
COLOR_RED = (255, 0, 0)
COLOR_GREEN = (0, 255, 0)
COLOR_BLUE = (0, 0, 255)
COLOR_OFF = (0, 0, 0)

# MESHブロックのシリアルナンバー識別子
# 実際のブロックのComplete Local Nameに含まれる識別子に合わせてください。
TEST_LED_SN = "TEST_LED_SN" 
TEST_BUTTON_SN = "TEST_BUTTON_SN"
TEST_GPIO_SN = "TEST_GPIO_SN" 
TEST_MOTION_SN = "TEST_MOTION_SN"

# グローバル変数 (テストの状態を管理)
test_clients = {
    "led": None,
    "button": None,
    "gpio": None,
    "motion": None
}

# 通知イベントキュー
button_event_queue = asyncio.Queue()
motion_orientation_event_queue = asyncio.Queue()

# ヘルパー関数
async def connect_to_mesh_block(address, block_id):
    try:
        client = BleakClient(address)
        print(f"Connecting to {block_id} ({address})...")
        await client.connect()
        print(f"Connected to {block_id}!")
        return client
    except Exception as e:
        print(f"Failed to connect to {block_id} ({address}): {e}")
        return None

async def set_led_state(client, color, blink=False):
    if not client or not client.is_connected:
        print("LED client not connected.")
        return
    
    blink_flag = 0x01 if blink else 0x00
    led_data = bytearray([CMD_ID_LED_CONTROL, color[0], color[1], color[2], blink_flag])
    try:
        await client.write_gatt_char(COMMAND_CHAR_UUID, led_data, response=False)
        print(f"Set LED to {color} (blink={blink})")
    except Exception as e:
        print(f"Error setting LED state: {e}")

async def play_buzzer_sound(client, duration_ms, frequency_hz=440):
    if not client or not client.is_connected:
        print("Buzzer client not connected.")
        return
    
    freq_bytes = frequency_hz.to_bytes(2, 'little')
    duration_bytes = duration_ms.to_bytes(2, 'little')
    buzzer_data = bytearray([
        CMD_ID_PWM_CONTROL,
        0x00, # Port (PWM Pin)
        0x00, # Mode (One-shot)
        freq_bytes[0], freq_bytes[1],
        0x00, 0x01, # Duty cycle (1000/1000)
        duration_bytes[0], duration_bytes[1]
    ])
    try:
        await client.write_gatt_char(COMMAND_CHAR_UUID, buzzer_data, response=False)
        print(f"Played buzzer for {duration_ms}ms at {frequency_hz}Hz")
    except Exception as e:
        print(f"Error playing buzzer: {e}")

# 通知ハンドラー
async def button_notification_handler(sender, data):
    if len(data) >= 2 and data[0] == NOTIF_ID_BUTTON_EVENT:
        button_state = data[1]
        print(f"Received button event: State={button_state}")
        await button_event_queue.put(button_state)

def motion_notification_handler(sender, data):
    if len(data) >= 3 and data[0] == NOTIF_ID_MOTION_ORIENTATION:
        orientation = data[2]
        print(f"Received motion event: Orientation={orientation}")
        # 最新の向きのみを保持
        while not motion_orientation_event_queue.empty():
            try:
                motion_orientation_event_queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
        motion_orientation_event_queue.put_nowait(orientation)

# テスト関数
async def test_led_block(led_client):
    print("\n--- LEDブロックのテスト ---")
    if not led_client:
        print("LEDブロックが接続されていません。スキップします。")
        return
    
    await set_led_state(led_client, COLOR_RED, blink=False)
    await asyncio.sleep(2)
    await set_led_state(led_client, COLOR_GREEN, blink=True)
    await asyncio.sleep(2)
    await set_led_state(led_client, COLOR_BLUE, blink=False)
    await asyncio.sleep(2)
    await set_led_state(led_client, COLOR_OFF)
    print("LEDテスト完了。")

async def test_button_block(button_client):
    print("\n--- ボタンブロックのテスト ---")
    if not button_client:
        print("ボタンブロックが接続されていません。スキップします。")
        return

    print("ボタンを短く押してください...")
    try:
        press_event = await asyncio.wait_for(button_event_queue.get(), timeout=5)
        if press_event == 0x01:
            print("短押しを検出しました。")
        else:
            print(f"期待しないイベントを検出: {press_event}")
    except asyncio.TimeoutError:
        print("タイムアウトしました。短押しは検出されませんでした。")
    
    print("ボタンを長押ししてください...")
    try:
        long_press_event = await asyncio.wait_for(button_event_queue.get(), timeout=5)
        if long_press_event == 0x02:
            print("長押しを検出しました。")
        else:
            print(f"期待しないイベントを検出: {long_press_event}")
    except asyncio.TimeoutError:
        print("タイムアウトしました。長押しは検出されませんでした。")
    
    print("ボタンテスト完了。")

async def test_gpio_block(gpio_client):
    print("\n--- GPIOブロック (ブザー) のテスト ---")
    if not gpio_client:
        print("GPIOブロックが接続されていません。スキップします。")
        return
    
    print("ブザーを短く鳴らします...")
    await play_buzzer_sound(gpio_client, 500)
    await asyncio.sleep(1)
    
    print("ブザーを長く鳴らします...")
    await play_buzzer_sound(gpio_client, 1000, frequency_hz=880)
    await asyncio.sleep(1.5)
    print("ブザーテスト完了。")

async def test_motion_block(motion_client):
    print("\n--- 動きブロックのテスト ---")
    if not motion_client:
        print("動きブロックが接続されていません。スキップします。")
        return

    print(f"動きブロックを「上」の向き（値: {ORIENTATION_UP}）にしてください。")
    try:
        target_orientation = await asyncio.wait_for(motion_orientation_event_queue.get(), timeout=10)
        if target_orientation == ORIENTATION_UP:
            print("「上」の向きを検出しました。")
        else:
            print(f"期待しない向きを検出: {target_orientation}")
    except asyncio.TimeoutError:
        print("タイムアウトしました。「上」の向きは検出されませんでした。")

    print(f"動きブロックを「裏」の向き（値: {ORIENTATION_BACK}）にしてください。")
    try:
        target_orientation = await asyncio.wait_for(motion_orientation_event_queue.get(), timeout=10)
        if target_orientation == ORIENTATION_BACK:
            print("「裏」の向きを検出しました。")
        else:
            print(f"期待しない向きを検出: {target_orientation}")
    except asyncio.TimeoutError:
        print("タイムアウトしました。「裏」の向きは検出されませんでした。")

    print("動きブロックテスト完了。")

async def main():
    global test_clients
    
    print("MESHブロックをスキャン中...")
    devices = await BleakScanner.discover(timeout=5.0)
    
    discovered_mesh_devices_by_sn_suffix = {}
    for d in devices:
        if d.name and d.name.startswith("MESH-"):
            sn_suffix = None
            if 'U' in d.name: sn_suffix = d.name[d.name.rfind('U')+1:]
            elif 'E' in d.name: sn_suffix = d.name[d.name.rfind('E')+1:]
            elif 'C' in d.name: sn_suffix = d.name[d.name.rfind('C')+1:]
            elif 'P' in d.name: sn_suffix = d.name[d.name.rfind('P')+1:]
            
            if sn_suffix:
                discovered_mesh_devices_by_sn_suffix[sn_suffix] = d
                print(f" - {d.name} -> シリアルナンバー識別子: {sn_suffix}")

    print("\nテスト対象ブロックに接続中...")
    
    # LEDブロックの接続
    led_device = discovered_mesh_devices_by_sn_suffix.get(TEST_LED_SN)
    if led_device:
        test_clients["led"] = await connect_to_mesh_block(led_device.address, "TEST_LED")
    else:
        print(f"Warning: LEDブロック (SN: {TEST_LED_SN}) が見つかりませんでした。")
        
    # ボタンブロックの接続
    button_device = discovered_mesh_devices_by_sn_suffix.get(TEST_BUTTON_SN)
    if button_device:
        test_clients["button"] = await connect_to_mesh_block(button_device.address, "TEST_BUTTON")
        if test_clients["button"]:
            try:
                await test_clients["button"].start_notify(NOTIFICATION_CHAR_UUID, button_notification_handler)
                print("Started button notifications.")
            except Exception as e:
                print(f"Error starting button notifications: {e}")
    else:
        print(f"Warning: ボタンブロック (SN: {TEST_BUTTON_SN}) が見つかりませんでした。")

    # GPIOブロックの接続
    gpio_device = discovered_mesh_devices_by_sn_suffix.get(TEST_GPIO_SN)
    if gpio_device:
        test_clients["gpio"] = await connect_to_mesh_block(gpio_device.address, "TEST_GPIO")
    else:
        print(f"Warning: GPIOブロック (SN: {TEST_GPIO_SN}) が見つかりませんでした。")
        
    # 動きブロックの接続
    motion_device = discovered_mesh_devices_by_sn_suffix.get(TEST_MOTION_SN)
    if motion_device:
        test_clients["motion"] = await connect_to_mesh_block(motion_device.address, "TEST_MOTION")
        if test_clients["motion"]:
            try:
                await test_clients["motion"].start_notify(NOTIFICATION_CHAR_UUID, motion_notification_handler)
                print("Started motion notifications.")
            except Exception as e:
                print(f"Error starting motion notifications: {e}")
    else:
        print(f"Warning: 動きブロック (SN: {TEST_MOTION_SN}) が見つかりませんでした。")

    print("\n--- テスト開始 ---")
    
    try:
        await test_led_block(test_clients["led"])
        await test_button_block(test_clients["button"])
        await test_gpio_block(test_clients["gpio"])
        await test_motion_block(test_clients["motion"])
    except Exception as e:
        print(f"テスト中にエラーが発生しました: {e}")
    finally:
        print("\nテスト終了。ブロックから切断します。")
        for client in test_clients.values():
            if client and client.is_connected:
                await client.disconnect()
        print("切断完了。")

if __name__ == "__main__":
    asyncio.run(main())