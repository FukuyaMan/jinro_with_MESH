import asyncio
import random
import time
from bleak import BleakClient, BleakScanner
from collections import Counter

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

# ゲーム設定
PLAYER_COUNT = 4
DISCUSSION_TIME_SECONDS = 60
PHASE_TIMEOUT_SECONDS = 10 # 夜の活動時間の各フェーズのタイムアウト

# ブロックのシリアルナンバー (ハードコード)
# 実際のブロックのComplete Local Nameに含まれる識別子に合わせてください。
# 例: "MESH-100BU1234567" の場合、"1234567" の部分をここに設定します。
PLAYER_LED_SN = { # 各プレイヤーのLEDブロックのシリアルナンバーサフィックス
    "player1": "LED_P1_SN", 
    "player2": "LED_P2_SN", 
    "player3": "LED_P3_SN", 
    "player4": "LED_P4_SN", 
}
PLAYER_BUTTON_SN = { # 各プレイヤーのボタンブロックのシリアルナンバーサフィックス
    "player1": "BTN_P1_SN", 
    "player2": "BTN_P2_SN", 
    "player3": "BTN_P3_SN", 
    "player4": "BTN_P4_SN", 
}
GPIO_BLOCK_SN = "GPIO_SN" # GPIOブロックのシリアルナンバーサフィックス
MOTION_BLOCK_SN = "MOTION_SN" # 動きブロックのシリアルナンバーサフィックス

# 色定義 (RGB値のタプル)
# MESHブロックのLEDが受け付ける形式に合わせて調整してください。
COLOR_RED = (255, 0, 0)
COLOR_GREEN = (0, 255, 0)
COLOR_BLUE = (0, 0, 255)
COLOR_YELLOW = (255, 255, 0)
COLOR_WHITE = (255, 255, 255)
COLOR_ORANGE = (255, 165, 0)
COLOR_PURPLE = (128, 0, 128)
COLOR_OFF = (0, 0, 0)

# 役職と対応するLED表示
ROLES = ["占い師", "怪盗", "市民", "市民", "人狼", "人狼"] # 6枚の役職カード
ROLE_LED_MAP = {
    "市民": {"color": COLOR_WHITE, "blink": False},
    "人狼": {"color": COLOR_WHITE, "blink": True},
    "怪盗": {"color": COLOR_ORANGE, "blink": False},
    "占い師": {"color": COLOR_PURPLE, "blink": False},
}

# グローバル変数 (ゲームの状態を管理)
# 各プレイヤーのLEDとボタンクライアントを個別に管理
player_clients = {} # {player_id: {"led": led_client, "button": button_client}}
player_roles = {}   # プレイヤーごとの役職 {player_id: role}
player_votes = {}   # 投票結果 {voter_id: voted_id}
gpio_client = None
motion_client = None
current_turn = "リセット"

# 通知イベントキュー
# ボタンイベントキュー: {player_id: asyncio.Queue()}
player_button_event_queues = {} 
# 動きセンサーイベントキュー: asyncio.Queue()
motion_orientation_event_queue = asyncio.Queue()

# ヘルパー関数

async def connect_to_mesh_block(address, block_id):
    # 指定されたアドレスのMESHブロックに接続
    try:
        client = BleakClient(address)
        print(f"Connecting to {block_id} ({address})...")
        await client.connect()
        print(f"Connected to {block_id}!")

        try:
            services = await client.get_services()
            # MESH_SERVICE_UUIDに属する特性を探す
            mesh_service = next((s for s in services if s.uuid == MESH_SERVICE_UUID), None)
            if mesh_service:
                for char in mesh_service.characteristics:
                    if char.uuid == STATE_INDICATION_CHAR_UUID:
                        await client.start_notify(STATE_INDICATION_CHAR_UUID, handle_state_indication)
                        print(f"Started state indications for {block_id}.")
                        break
                else:
                    print(f"Warning: State Indication Characteristic ({STATE_INDICATION_CHAR_UUID}) not found in {MESH_SERVICE_UUID} for {block_id}.")
            else:
                print(f"Warning: MESH Service ({MESH_SERVICE_UUID}) not found for {block_id}.")
        except Exception as e:
            print(f"Error starting state indications for {block_id}: {e}")

        return client
    except Exception as e:
        print(f"Failed to connect to {block_id} ({address}): {e}")
        return None

async def set_led_state(client, color, blink=False):
    # LEDの色を設定し、点滅させるかどうかを制御
    if not client or not client.is_connected:
        print("LED client not connected.")
        return
    
    blink_flag = 0x01 if blink else 0x00
    led_data = bytearray([CMD_ID_LED_CONTROL, color[0], color[1], color[2], blink_flag])
    try:
        # write_gatt_charのresponse=FalseはWrite Without Response
        await client.write_gatt_char(COMMAND_CHAR_UUID, led_data, response=False)
        # print(f"Set LED to {color} (blink={blink}) for {client.address}")
    except Exception as e:
        print(f"Error setting LED state for {client.address}: {e}")

async def play_buzzer_sound(client, duration_ms, frequency_hz=440, duty_cycle_permillage=500):
    # GPIOブロックのブザーを鳴らす
    if not client or not client.is_connected:
        print("Buzzer client not connected.")
        return
    
    # 周波数、デューティサイクル、持続時間をバイト配列に変換
    freq_bytes = frequency_hz.to_bytes(2, 'little') # MESHはリトルエンディアン
    duty_bytes = duty_cycle_permillage.to_bytes(2, 'little')
    duration_bytes = duration_ms.to_bytes(2, 'little')

    buzzer_data = bytearray([
        CMD_ID_PWM_CONTROL,
        0x00, # Port (PWM Pin)
        0x00, # Mode (One-shot)
        freq_bytes[0], freq_bytes[1],
        duty_bytes[0], duty_bytes[1],
        duration_bytes[0], duration_bytes[1]
    ])
    try:
        # write_gatt_charのresponse=FalseはWrite Without Response
        await client.write_gatt_char(COMMAND_CHAR_UUID, buzzer_data, response=False)
        # print(f"Played buzzer for {duration_ms}ms at {frequency_hz}Hz")
    except Exception as e:
        print(f"Error playing buzzer for {client.address}: {e}")

# 通知ハンドラー
def button_notification_handler_factory(player_id):
    # ボタン通知ハンドラーを生成するファクトリ関数
    async def handler(sender, data):
        # data: [通知ID (0x01), ボタン状態 (0x00:離, 0x01:押, 0x02:長押)]
        if len(data) >= 2 and data[0] == NOTIF_ID_BUTTON_EVENT:
            button_state = data[1]
            # print(f"Button event from {player_id}: State={button_state}")
            await player_button_event_queues[player_id].put(button_state)
    return handler

def motion_notification_handler(sender, data):
    # 動きブロック通知ハンドラー
    # data: [通知ID (0x03), Reserved (0x00), 向き (1 byte)]
    # 向きの値はデータ位置2 (0-indexed)
    if len(data) >= 3 and data[0] == NOTIF_ID_MOTION_ORIENTATION:
        orientation = data[2] # 向きの値はデータ位置2
        # print(f"Motion event: Orientation={orientation}")
        # 最新の向きのみを保持するためにキューをクリアしてから追加
        while not motion_orientation_event_queue.empty():
            try:
                motion_orientation_event_queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
        motion_orientation_event_queue.put_nowait(orientation)

def handle_state_indication(sender, data):
    # STATE_INDICATION_CHAR_UUID からの通知を処理するハンドラー
    print(f"Received state indication from {sender}: {data.hex()}")

# ボタン/動きセンサー待機関数
async def wait_for_button_press(button_client, timeout=None):
    # ボタンが押されるまで待機
    player_id = next((p_id for p_id, data in player_clients.items() if data["button"] == button_client), None)
    if not player_id:
        print("Error: Could not find player_id for button client.")
        return False

    start_time = time.time()
    while True:
        try:
            # ボタンが押された (0x01) または長押しされた (0x02) イベントを待つ
            button_state = await asyncio.wait_for(player_button_event_queues[player_id].get(), timeout=timeout)
            
            if button_state == 0x01 or button_state == 0x02: # 押された、または長押し
                # MESHの通知はPressとRelease両方送るので、Releaseを待つのが確実
                # ただし、ここでは単一のプレスイベントを検出する
                return True
            
        except asyncio.TimeoutError:
            return False # タイムアウト
        except Exception as e:
            print(f"Error waiting for button press: {e}")
            return False
        
        # タイムアウトが設定されていて、時間が経過した場合
        if timeout and (time.time() - start_time > timeout):
            return False

async def wait_for_long_press(button_client, long_press_duration=1.5):
    # ボタンが長押しされるまで待機
    player_id = next((p_id for p_id, data in player_clients.items() if data["button"] == button_client), None)
    if not player_id:
        print("Error: Could not find player_id for button client.")
        return False

    start_time = time.time()
    while True:
        try:
            # 長押し (0x02) イベントを待つ
            button_state = await asyncio.wait_for(player_button_event_queues[player_id].get(), timeout=PHASE_TIMEOUT_SECONDS)
            if button_state == 0x02: # 長押しイベントを直接検出
                return True
        except asyncio.TimeoutError:
            return False # タイムアウト
        except Exception as e:
            print(f"Error waiting for long press: {e}")
            return False
        
        if (time.time() - start_time > PHASE_TIMEOUT_SECONDS):
            return False

async def wait_for_motion_orientation(motion_client, target_orientation_value):
    # 動きブロックが特定の向きになるまで待機
    while True:
        try:
            current_orientation = await asyncio.wait_for(motion_orientation_event_queue.get(), timeout=None) # タイムアウトなしで永久に待つ
            if current_orientation == target_orientation_value:
                print(f"Motion block is now in target orientation: {target_orientation_value}")
                return True
        except Exception as e:
            print(f"Error waiting for motion orientation: {e}")
            await asyncio.sleep(0.1) # エラー時の待機

# ゲームフェーズ関数

async def reset_game(clients):
    # リセットターン
    global current_turn
    current_turn = "リセット"
    print("\nリセットターン")

    # 全てのLEDを消灯
    for player_id, player_data in clients.items():
        if player_data["led"]:
            await set_led_state(player_data["led"], COLOR_OFF)
    
    # ブザーを短く1回鳴らす
    await play_buzzer_sound(gpio_client, 200) # 200ms

    print("ゲーム開始準備ができました。各プレイヤーのボタンを押してください。")
    # 全てのプレイヤーのボタンが押されるまで待機
    await asyncio.gather(*[wait_for_button_press(player_data["button"]) for player_data in clients.values() if player_data["button"]])
    print("全てのプレイヤーが準備完了しました。")

async def distribute_roles(clients):
    # 役職配布ターン
    global player_roles, current_turn
    current_turn = "役職配布"
    print("\n役職配布ターン")

    # 動きブロックが「左」になるのを待つ (ORIENTATION_LEFT)
    print("動きブロックを「左」の向きにしてください。")
    await wait_for_motion_orientation(motion_client, ORIENTATION_LEFT)

    # ブザーを長く1回鳴らす
    await play_buzzer_sound(gpio_client, 1000) # 1000ms

    # 役職のランダム割り当て
    assigned_roles = random.sample(ROLES, PLAYER_COUNT)
    player_ids = list(clients.keys())
    random.shuffle(player_ids) # プレイヤーの順序もランダムに
    
    player_roles = {player_id: role for player_id, role in zip(player_ids, assigned_roles)}

    print("役職を配布しました。")
    # 各プレイヤーのLEDに役職を表示
    for player_id, role in player_roles.items():
        config = ROLE_LED_MAP[role]
        if clients[player_id]["led"]:
            await set_led_state(clients[player_id]["led"], config["color"], config["blink"])
            print(f"{player_id}: {role} ({'点滅' if config['blink'] else '点灯'})") # 実際のゲームでは表示しない

    print("各プレイヤーは自分の役職を確認し、ボタンを押してください。")
    # 全てのプレイヤーが自分の役職を確認し、ボタンを押すまで待機
    await asyncio.gather(*[wait_for_button_press(player_data["button"]) for player_data in clients.values() if player_data["button"]])
    print("全てのプレイヤーが役職を確認しました。")

async def night_activity_phase(clients):
    # 夜の活動時間ターン
    global current_turn
    current_turn = "夜の活動時間"
    print("\n夜の活動時間ターン")

    # 動きブロックが「上」になるのを待つ (ORIENTATION_UP)
    print("動きブロックを「上」の向きにしてください。")
    await wait_for_motion_orientation(motion_client, ORIENTATION_UP)

    # ブザーを長く1回鳴らす
    await play_buzzer_sound(gpio_client, 1000) # 1000ms

    print("夜の活動時間です。各プレイヤーはうつ伏せになり、ボタンを押してください。")
    # 全てのプレイヤーのボタンが「うつ伏せになったことを示す」ために押されるのを待つ
    await asyncio.gather(*[wait_for_button_press(player_data["button"]) for player_data in clients.values() if player_data["button"]])
    print("全員うつ伏せになりました。夜の活動を開始します。")

    # フェーズの順次進行 (ブザーなし)
    print("\n占い師フェーズ")
    await run_seer_phase(clients)

    print("\n人狼フェーズ")
    await run_werewolf_phase(clients)

    print("\n怪盗フェーズ")
    await run_thief_phase(clients)

    # 怪盗の操作後、ブザーを鳴らし全員を起こす
    await play_buzzer_sound(gpio_client, 1500) # 長めに鳴らす

async def run_seer_phase(clients):
    # 占い師の活動フェーズ
    seer_id = next((p_id for p_id, role in player_roles.items() if role == "占い師"), None)
    
    if seer_id and clients[seer_id]["led"] and clients[seer_id]["button"]:
        seer_led_client = clients[seer_id]["led"]
        seer_button_client = clients[seer_id]["button"]
        print(f"占い師 ({seer_id}) の活動時間です。")
        await set_led_state(seer_led_client, ROLE_LED_MAP["占い師"]["color"]) # 紫点灯

        target_player_id = None
        player_list = list(clients.keys())
        current_target_index = 0

        async def select_target_logic():
            nonlocal target_player_id, current_target_index
            while True:
                # ボタンイベントを待つ
                button_state = await asyncio.wait_for(player_button_event_queues[seer_id].get(), timeout=None) # 無限に待つ

                if button_state == 0x01: # 短押し
                    current_target_index = (current_target_index + 1) % PLAYER_COUNT
                    target_player_id = player_list[current_target_index] # 更新
                    print(f"占い師が {target_player_id} を選択中...")
                    # 選択中のプレイヤーのLEDをそのプレイヤーの色で点灯
                    for pid, pdata in clients.items():
                        if pdata["led"]:
                            if pid == target_player_id:
                                await set_led_state(pdata["led"], PLAYER_COLORS[pid])
                            else:
                                await set_led_state(pdata["led"], COLOR_OFF)
                    await asyncio.sleep(0.5) # 次の短押しまで少し待つ
                elif button_state == 0x02: # 長押しで決定
                    target_player_id = player_list[current_target_index]
                    print(f"占い師が {target_player_id} を長押しで決定しました。")
                    return True # 長押しで決定
                
        try:
            # ターゲット選択とタイムアウト
            select_task = asyncio.create_task(select_target_logic())
            timeout_task = asyncio.create_task(asyncio.sleep(PHASE_TIMEOUT_SECONDS))
            
            done, pending = await asyncio.wait([select_task, timeout_task], return_when=asyncio.FIRST_COMPLETED)

            if select_task in done and select_task.result(): # 長押しで決定された場合
                # 占った人の陣営の色を表示
                target_role = player_roles[target_player_id]
                config = ROLE_LED_MAP[target_role]
                print(f"{target_player_id} の役職は {target_role} です。")
                await set_led_state(seer_led_client, config["color"], config["blink"]) # 占い師のLEDに表示
                
                print("占い師は確認後、ボタンを押してください。")
                # 確認ボタンが押されるまで待つ
                await asyncio.wait_for(wait_for_button_press(seer_button_client), timeout=PHASE_TIMEOUT_SECONDS)
            else: # タイムアウトした場合
                print("占い師は時間内に操作を行いませんでした。")
            
            # 残りのタスクをキャンセル
            for task in pending:
                task.cancel()

        except asyncio.CancelledError:
            print("占い師フェーズがキャンセルされました。")
        finally:
            if seer_led_client:
                await set_led_state(seer_led_client, COLOR_OFF) # 占い師のLEDを消灯
            for pid, pdata in clients.items(): # 全てのプレイヤーのLEDを消灯
                if pdata["led"]:
                    await set_led_state(pdata["led"], COLOR_OFF)
    else:
        print("占い師はいません、またはブロックが接続されていません。10秒間待機します。")
        await asyncio.sleep(PHASE_TIMEOUT_SECONDS)

async def run_werewolf_phase(clients):
    # 人狼の活動フェーズ
    werewolf_ids = [p_id for p_id, role in player_roles.items() if role == "人狼"]
    
    if werewolf_ids:
        print(f"人狼 ({', '.join(werewolf_ids)}) の活動時間です。")
        button_clients_to_wait = []
        for w_id in werewolf_ids:
            if clients[w_id]["led"]:
                await set_led_state(clients[w_id]["led"], ROLE_LED_MAP["人狼"]["color"], ROLE_LED_MAP["人狼"]["blink"]) # 白点滅
            if clients[w_id]["button"]:
                button_clients_to_wait.append(clients[w_id]["button"])


        print("人狼は確認後、ボタンを押してください。")
        
        button_press_tasks = [wait_for_button_press(btn_client) for btn_client in button_clients_to_wait]
        
        try:
            # 全ての人狼のボタンが押されるか、タイムアウト
            done, pending = await asyncio.wait(
                button_press_tasks + [asyncio.sleep(PHASE_TIMEOUT_SECONDS)],
                return_when=asyncio.FIRST_COMPLETED if len(werewolf_ids) == 1 else asyncio.ALL_COMPLETED
            )

            if asyncio.sleep(PHASE_TIMEOUT_SECONDS) in done: # タイムアウトした場合
                print("人狼は時間内に操作を行いませんでした。")
            else:
                print("人狼が確認しました。")
            
            # 残りのタスクをキャンセル
            for task in pending:
                task.cancel()

        except asyncio.CancelledError:
            print("人狼フェーズがキャンセルされました。")
        finally:
            for w_id in werewolf_ids:
                if clients[w_id]["led"]:
                    await set_led_state(clients[w_id]["led"], COLOR_OFF) # 人狼のLEDを消灯
    else:
        print("人狼はいません。10秒間待機します。")
        await asyncio.sleep(PHASE_TIMEOUT_SECONDS)

async def run_thief_phase(clients):
    # 怪盗の活動フェーズ
    thief_id = next((p_id for p_id, role in player_roles.items() if role == "怪盗"), None)
    
    if thief_id and clients[thief_id]["led"] and clients[thief_id]["button"]:
        thief_led_client = clients[thief_id]["led"]
        thief_button_client = clients[thief_id]["button"]
        print(f"怪盗 ({thief_id}) の活動時間です。")
        await set_led_state(thief_led_client, ROLE_LED_MAP["怪盗"]["color"]) # オレンジ点灯

        target_player_id = None
        player_list = list(clients.keys())
        current_target_index = 0

        async def select_target_and_swap_logic():
            nonlocal target_player_id, current_target_index
            while True:
                # ボタンイベントを待つ
                button_state = await asyncio.wait_for(player_button_event_queues[thief_id].get(), timeout=None) # 無限に待つ

                if button_state == 0x01: # 短押し
                    current_target_index = (current_target_index + 1) % PLAYER_COUNT
                    target_player_id = player_list[current_target_index] # 更新
                    print(f"怪盗が {target_player_id} を選択中...")
                    # 選択中のプレイヤーのLEDをそのプレイヤーの色で点灯
                    for pid, pdata in clients.items():
                        if pdata["led"]:
                            if pid == target_player_id:
                                await set_led_state(pdata["led"], PLAYER_COLORS[pid])
                            else:
                                await set_led_state(pdata["led"], COLOR_OFF)
                    await asyncio.sleep(0.5) # 次の短押しまで少し待つ
                elif button_state == 0x02: # 長押しで決定
                    target_player_id = player_list[current_target_index]
                    print(f"怪盗が {target_player_id} を長押しで決定しました。")
                    
                    # 役職の交換
                    original_thief_role = player_roles[thief_id] # 怪盗自身の元の役職
                    target_original_role = player_roles[target_player_id] # ターゲットの元の役職
                    
                    player_roles[thief_id] = target_original_role # 怪盗はターゲットの役職に
                    player_roles[target_player_id] = original_thief_role # ターゲットは怪盗の役職に (怪盗カードは場からなくなる)

                    print(f"怪盗が {target_player_id} と役職を交換しました。")
                    print(f"怪盗の新しい役職: {player_roles[thief_id]}")

                    # 交換後の怪盗の役職の色を点灯/点滅
                    new_thief_role_config = ROLE_LED_MAP[player_roles[thief_id]]
                    await set_led_state(thief_led_client, new_thief_role_config["color"], new_thief_role_config["blink"])
                    return True # 長押しで決定
                
        try:
            # ターゲット選択と役職交換、タイムアウト
            select_task = asyncio.create_task(select_target_and_swap_logic())
            timeout_task = asyncio.create_task(asyncio.sleep(PHASE_TIMEOUT_SECONDS))
            
            done, pending = await asyncio.wait([select_task, timeout_task], return_when=asyncio.FIRST_COMPLETED)

            if select_task in done and select_task.result(): # 長押しで決定された場合
                print("怪盗は確認後、ボタンを押してください。")
                # 確認ボタンが押されるまで待つ
                await asyncio.wait_for(wait_for_button_press(thief_button_client), timeout=PHASE_TIMEOUT_SECONDS)
            else: # タイムアウトした場合
                print("怪盗は時間内に操作を行いませんでした。役職は交換されません。")
            
            # 残りのタスクをキャンセル
            for task in pending:
                task.cancel()

        except asyncio.CancelledError:
            print("怪盗フェーズがキャンセルされました。")
        finally:
            if thief_led_client:
                await set_led_state(thief_led_client, COLOR_OFF) # 怪盗のLEDを消灯
            for pid, pdata in clients.items(): # 全てのプレイヤーのLEDを消灯
                if pdata["led"]:
                    await set_led_state(pdata["led"], COLOR_OFF)
    else:
        print("怪盗はいません、またはブロックが接続されていません。10秒間待機します。")
        await asyncio.sleep(PHASE_TIMEOUT_SECONDS)


async def day_discussion_phase(clients):
    # 昼の議論時間ターン
    global current_turn
    current_turn = "昼の議論時間"
    print("\n昼の議論時間ターン")

    # 動きブロックが「右」になるのを待つ (ORIENTATION_RIGHT)
    print("動きブロックを「右」の向きにしてください。")
    await wait_for_motion_orientation(motion_client, ORIENTATION_RIGHT)

    # ブザーを長く1回鳴らす
    await play_buzzer_sound(gpio_client, 1000) # 1000ms

    print(f"議論時間開始！ ({DISCUSSION_TIME_SECONDS}秒)")
    await asyncio.sleep(DISCUSSION_TIME_SECONDS)
    print("議論時間終了！")

    # ブザーを長く1回鳴らす
    await play_buzzer_sound(gpio_client, 1000) # 1000ms

    print("動きブロックを「裏」の向きにしてください。") # ブザー音のみで促す

async def voting_phase(clients):
    # 投票時間ターン
    global current_turn, player_votes
    current_turn = "投票時間"
    player_votes = {} # 投票結果をリセット
    print("\n投票時間ターン")

    # 動きブロックが「裏」になるのを待つ (ORIENTATION_BACK)
    print("動きブロックを「裏」の向きにしてください。")
    await wait_for_motion_orientation(motion_client, ORIENTATION_BACK)

    # ブザーを長く1回鳴らす
    await play_buzzer_sound(gpio_client, 1000) # 1000ms

    print("投票を開始します。各プレイヤーは投票相手を選んで長押しで確定してください。")

    vote_tasks = []
    for voter_id, player_data in clients.items():
        if player_data["button"] and player_data["led"]:
            async def get_vote(voter_id, voter_button_client, voter_led_client):
                target_player_id = None
                player_list = list(clients.keys())
                current_target_index = 0

                # 自分のLEDを点灯（投票中であることを示す）
                await set_led_state(voter_led_client, PLAYER_COLORS[voter_id])

                while True:
                    # ボタンイベントを待つ
                    button_state = await asyncio.wait_for(player_button_event_queues[voter_id].get(), timeout=None) # 無限に待つ

                    if button_state == 0x01: # 短押し
                        current_target_index = (current_target_index + 1) % PLAYER_COUNT
                        target_player_id = player_list[current_target_index] # 更新
                        
                        # 選択中のプレイヤーのLEDをそのプレイヤーの色で点灯（一時的に）
                        # 他のプレイヤーのLEDは触らない
                        temp_target_led_client = clients[target_player_id]["led"]
                        if temp_target_led_client:
                            await set_led_state(temp_target_led_client, PLAYER_COLORS[target_player_id])
                            await asyncio.sleep(0.3) # 短く点灯
                            await set_led_state(temp_target_led_client, COLOR_OFF) # 消灯

                    elif button_state == 0x02: # 長押しで確定
                        target_player_id = player_list[current_target_index]
                        player_votes[voter_id] = target_player_id
                        print(f"{voter_id} が {target_player_id} に投票しました。")
                        await set_led_state(voter_led_client, COLOR_OFF) # 投票完了でLEDを消灯
                        return # 投票完了
            vote_tasks.append(asyncio.create_task(get_vote(voter_id, player_data["button"], player_data["led"])))

    await asyncio.gather(*vote_tasks)
    print("全ての投票が完了しました。")

    # ブザーを鳴らす
    await play_buzzer_sound(gpio_client, 1000)

    # 投票結果の表示
    if not player_votes:
        print("投票が行われませんでした。")
        return

    vote_counts = Counter(player_votes.values())
    max_votes = 0
    if vote_counts:
        max_votes = max(vote_counts.values())

    most_voted_players = [p_id for p_id, count in vote_counts.items() if count == max_votes]

    print(f"最も多く投票されたプレイヤー: {', '.join(most_voted_players)} ({max_votes}票)")

    # 最も多く投票されたプレイヤーのLEDを5回点滅
    for p_id in most_voted_players:
        if clients[p_id]["led"]:
            client = clients[p_id]["led"]
            for _ in range(5):
                await set_led_state(client, PLAYER_COLORS[p_id])
                await asyncio.sleep(0.2)
                await set_led_state(client, COLOR_OFF)
                await asyncio.sleep(0.2)
    await asyncio.sleep(1) # 点滅後少し待つ

    return most_voted_players

async def determine_and_display_winner(clients, most_voted_players):
    # 勝敗判定
    print("\n勝敗判定")

    # 処刑されたプレイヤー
    executed_players = []
    if len(most_voted_players) == 1:
        executed_players.append(most_voted_players[0])
    elif len(most_voted_players) > 1: # 同数票の場合
        executed_players.extend(most_voted_players)
    
    print(f"処刑されたプレイヤー: {', '.join(executed_players) if executed_players else 'なし'}")

    # 処刑後の人狼の数を数える
    remaining_werewolves = 0
    for p_id, role in player_roles.items():
        if role == "人狼" and p_id not in executed_players:
            remaining_werewolves += 1

    winning_team = None
    if not executed_players: # 処刑される人がいない場合
        if remaining_werewolves == 0:
            winning_team = "全員" # 人狼が1人もいない場合は、プレイヤー全員の勝利
        else:
            winning_team = "人狼チーム" # 人狼が1人でも残っていた場合は、人狼チームの勝利
    else: # 処刑された人がいる場合
        werewolf_executed = any(player_roles[p_id] == "人狼" for p_id in executed_players)
        if werewolf_executed:
            winning_team = "市民チーム" # 処刑された2人のどちらかに人狼がいれば人間チームの勝利
        else:
            winning_team = "人狼チーム" # 両方とも市民だった場合、人狼チームの勝利

    print(f"勝利チーム: {winning_team}")

    # LED表示
    winning_players = []
    losing_players = []

    if winning_team == "全員":
        winning_players = list(clients.keys())
    elif winning_team == "市民チーム":
        for p_id, role in player_roles.items():
            if role in ["市民", "占い師", "怪盗"]:
                winning_players.append(p_id)
            elif role == "人狼":
                losing_players.append(p_id)
    elif winning_team == "人狼チーム":
        for p_id, role in player_roles.items():
            if role == "人狼":
                winning_players.append(p_id)
            else:
                losing_players.append(p_id)

    print("勝敗結果表示")
    for p_id, player_data in clients.items():
        if player_data["led"]:
            if p_id in winning_players:
                await set_led_state(player_data["led"], COLOR_BLUE, blink=True) # 勝利したプレイヤーは青色に点滅
                print(f"{p_id} (勝利): 青色点滅")
            elif p_id in losing_players:
                await set_led_state(player_data["led"], COLOR_OFF) # 敗北したプレイヤーは消灯
                print(f"{p_id} (敗北): 消灯")
            else: # 処刑されたが勝敗に関わらない場合など、念のため消灯
                await set_led_state(player_data["led"], COLOR_OFF)


    await play_buzzer_sound(gpio_client, 2000) # 長く鳴らす
    await asyncio.sleep(5) # 結果表示のために5秒間待機
    
    # 全てのLEDを消灯して終了
    for player_data in clients.values():
        if player_data["led"]:
            await set_led_state(player_data["led"], COLOR_OFF)

# メイン関数
async def main():
    global player_clients, gpio_client, motion_client

    print("MESHブロックをスキャン中...")
    devices = await BleakScanner.discover(timeout=5.0)
    
    # 検出されたMESHブロックを識別子でマッピングするための辞書
    # {シリアルナンバーSuffix: BleakDeviceオブジェクト}
    discovered_mesh_devices_by_sn_suffix = {}
    
    print("検出されたデバイス:")
    for d in devices:
        if d.name and d.name.startswith("MESH-"):
            print(f"  Name: {d.name}, Address: {d.address}")
            # MESH-100BU, MESH-100LE, MESH-100AC, MESH-100GP
            # 最後の 'U', 'E', 'C', 'P' の後から最後までがシリアルナンバーのサフィックス
            sn_suffix = None
            if 'U' in d.name: # Button Block
                sn_suffix = d.name[d.name.rfind('U')+1:]
            elif 'E' in d.name: # LED Block
                sn_suffix = d.name[d.name.rfind('E')+1:]
            elif 'C' in d.name: # Motion Block (AC)
                sn_suffix = d.name[d.name.rfind('C')+1:]
            elif 'P' in d.name: # GPIO Block (GP)
                sn_suffix = d.name[d.name.rfind('P')+1:]
            
            if sn_suffix:
                discovered_mesh_devices_by_sn_suffix[sn_suffix] = d
                print(f"    -> シリアルナンバー識別子: {sn_suffix}")
            else:
                print(f"    -> シリアルナンバー識別子を抽出できませんでした。")


    print("\nMESHブロックに接続中...")
    
    # プレイヤーブロックの接続 (LEDとボタンを個別に)
    for i in range(1, PLAYER_COUNT + 1):
        player_id = f"player{i}"
        player_clients[player_id] = {"led": None, "button": None} # 初期化
        player_button_event_queues[player_id] = asyncio.Queue() # 各プレイヤーのボタンイベントキューを初期化

        # LEDブロックの接続
        led_sn = PLAYER_LED_SN.get(player_id)
        if led_sn and led_sn in discovered_mesh_devices_by_sn_suffix:
            led_device = discovered_mesh_devices_by_sn_suffix[led_sn]
            led_client = await connect_to_mesh_block(led_device.address, f"{player_id}_LED")
            if led_client:
                player_clients[player_id]["led"] = led_client
            else:
                print(f"Warning: {player_id} のLEDブロック ({led_sn}) に接続できませんでした。")
        else:
            print(f"Warning: {player_id} のLEDブロック (SN: {led_sn}) が見つかりませんでした。")

        # ボタンブロックの接続
        button_sn = PLAYER_BUTTON_SN.get(player_id)
        if button_sn and button_sn in discovered_mesh_devices_by_sn_suffix:
            button_device = discovered_mesh_devices_by_sn_suffix[button_sn]
            button_client = await connect_to_mesh_block(button_device.address, f"{player_id}_BUTTON")
            if button_client:
                player_clients[player_id]["button"] = button_client
                # ボタン通知を開始
                try:
                    await button_client.start_notify(NOTIFICATION_CHAR_UUID, button_notification_handler_factory(player_id))
                    print(f"Started button notifications for {player_id}")
                except Exception as e:
                    print(f"Error starting button notifications for {player_id}: {e}")
            else:
                print(f"Warning: {player_id} のボタンブロック ({button_sn}) に接続できませんでした。")
        else:
            print(f"Warning: {player_id} のボタンブロック (SN: {button_sn}) が見つかりませんでした。")

    # GPIOブロックの接続
    gpio_device = discovered_mesh_devices_by_sn_suffix.get(GPIO_BLOCK_SN)
    if gpio_device:
        gpio_client = await connect_to_mesh_block(gpio_device.address, "gpio_block")
        if not gpio_client:
            print("Warning: GPIOブロックに接続できませんでした。ブザーは機能しません。")
    else:
        print(f"Warning: GPIOブロック (SN: {GPIO_BLOCK_SN}) が見つかりませんでした。")

    # 動きブロックの接続
    motion_device = discovered_mesh_devices_by_sn_suffix.get(MOTION_BLOCK_SN)
    if motion_device:
        motion_client = await connect_to_mesh_block(motion_device.address, "motion_block")
        if not motion_client:
            print("Warning: 動きブロックに接続できませんでした。ターンの遷移は機能しません。")
        else:
            # 動きセンサー通知を開始
            try:
                await motion_client.start_notify(NOTIFICATION_CHAR_UUID, motion_notification_handler)
                print("Started motion notifications.")
            except Exception as e:
                print(f"Error starting motion notifications: {e}")
    else:
        print(f"Warning: 動きブロック (SN: {MOTION_BLOCK_SN}) が見つかりませんでした。")

    # 全ての必須ブロックが接続されているか確認
    all_players_connected = True
    for player_id, clients_data in player_clients.items():
        if not clients_data["led"] or not clients_data["button"]:
            all_players_connected = False
            print(f"Error: {player_id} のLEDまたはボタンブロックが接続されていません。")
            break

    if not all_players_connected or not gpio_client or not motion_client:
        print("必要な全てのMESHブロックに接続できませんでした。ゲームを開始できません。")
        # 接続できなかったクライアントをクローズ
        for player_id, clients_data in player_clients.items():
            if clients_data["led"] and clients_data["led"].is_connected:
                await clients_data["led"].disconnect()
            if clients_data["button"] and clients_data["button"].is_connected:
                await clients_data["button"].disconnect()
        if gpio_client and gpio_client.is_connected:
            await gpio_client.disconnect()
        if motion_client and motion_client.is_connected:
            await motion_client.disconnect()
        return

    print("\n全てのMESHブロックに接続しました。ゲームを開始します。")

    try:
        # ゲームの各ターンを順番に実行
        await reset_game(player_clients)
        await distribute_roles(player_clients)
        await night_activity_phase(player_clients)
        await day_discussion_phase(player_clients)
        most_voted = await voting_phase(player_clients)
        if most_voted is not None:
            await determine_and_display_winner(player_clients, most_voted)
        else:
            print("投票が正常に行われなかったため、勝敗判定をスキップします。")

    except Exception as e:
        print(f"ゲーム中にエラーが発生しました: {e}")
    finally:
        print("\nゲーム終了。全てのMESHブロックから切断します。")
        # 全てのクライアントを切断
        for player_id, clients_data in player_clients.items():
            if clients and clients_data["led"] and clients_data["led"].is_connected:
                # 通知を停止 (LEDブロックは通常Notify/Indicateを送信しないが、念のため)
                try:
                    await clients_data["led"].stop_notify(NOTIFICATION_CHAR_UUID)
                    await clients_data["led"].stop_notify(STATE_INDICATION_CHAR_UUID)
                except Exception:
                    pass # エラーを無視
                await clients_data["led"].disconnect()
            if clients and clients_data["button"] and clients_data["button"].is_connected:
                # 通知を停止
                try:
                    await clients_data["button"].stop_notify(NOTIFICATION_CHAR_UUID)
                    await clients_data["button"].stop_notify(STATE_INDICATION_CHAR_UUID)
                except Exception as e:
                    print(f"Error stopping button notifications for {player_id}: {e}")
                await clients_data["button"].disconnect()
        if gpio_client and gpio_client.is_connected:
            # 通知を停止 (GPIOブロックは通常Notify/Indicateを送信しないが、念のため)
            try:
                await gpio_client.stop_notify(NOTIFICATION_CHAR_UUID)
                await gpio_client.stop_notify(STATE_INDICATION_CHAR_UUID)
            except Exception:
                pass # エラーを無視
            await gpio_client.disconnect()
        if motion_client and motion_client.is_connected:
            # 通知を停止
            try:
                await motion_client.stop_notify(NOTIFICATION_CHAR_UUID)
                await motion_client.stop_notify(STATE_INDICATION_CHAR_UUID)
            except Exception as e:
                print(f"Error stopping motion notifications: {e}")
            await motion_client.disconnect()
        print("切断完了。")

if __name__ == "__main__":
    # プレイヤーの色のマッピングを初期化
    # プレイヤーIDと色の対応を定義
    PLAYER_COLORS = {
        "player1": COLOR_RED,
        "player2": COLOR_GREEN,
        "player3": COLOR_BLUE,
        "player4": COLOR_YELLOW,
    }
    asyncio.run(main())