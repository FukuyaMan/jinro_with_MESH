import asyncio
import time
from bleak import BleakClient, BleakScanner, discover
from collections import Counter
from struct import pack

# MESHブロックの共通サービスUUIDと特性UUID
# 全てのMESHブロックが持つ共通サービスUUID
MESH_SERVICE_UUID = ('72c90001-57a9-4d40-b746-534e22ec9f9e')

# UUID
CORE_INDICATE_UUID = ('72c90005-57a9-4d40-b746-534e22ec9f9e')
CORE_NOTIFY_UUID = ('72c90003-57a9-4d40-b746-534e22ec9f9e')
CORE_WRITE_UUID = ('72c90004-57a9-4d40-b746-534e22ec9f9e')

# Contents values
MESSAGE_TYPE_INDEX = 0
EVENT_TYPE_INDEX = 1
MESSAGE_TYPE_ID = 1
# LED
LE_WRITE_R_INDEX = 2
LE_WRITE_G_INDEX = 4
LE_WRITE_B_INDEX = 6
# ボタン
BU_STATE_INDEX = 2
BU_EVENT_TYPE_ID = 0x00
# 動き
AC_STATE_INDEX = 2
AC_EVENT_TYPE_ID = 0x03
AC_LEFT = 0x01
AC_UP = 0x05
AC_RIGHT = 0x06
AC_FRONT = 0x03
AC_BACK = 0x04
# GPIO
GP_PWM_INDEX = 5

# Serial Number
SN_LE = "MESH-100LE1027271" 
SN_BU = "MESH-100BU1029369"
SN_AC = "MESH-100AC1029724" 
SN_GP = "MESH-100GP1050119"

# グローバル変数
test_clients = {
    "led": None,
    "button": None,
    "gpio": None,
    "motion": None
}

# Callback
def on_BU_receive_notify(sender, data: bytearray):
    if data[MESSAGE_TYPE_INDEX] != MESSAGE_TYPE_ID and data[EVENT_TYPE_INDEX] != BU_EVENT_TYPE_ID:
        return
    if data[BU_STATE_INDEX] == 1:
        print('Single Pressed.')
        return
    if data[BU_STATE_INDEX] == 2:
        print('Long Pressed.')
        return
    if data[BU_STATE_INDEX] == 3:
        print('Double Pressed.')
        return

def on_AC_receive_notify(sender, data: bytearray):
    if data[MESSAGE_TYPE_INDEX] != MESSAGE_TYPE_ID and data[AC_EVENT_TYPE_INDEX] != AC_EVENT_TYPE_ID:
        return
    if data[AC_STATE_INDEX] == AC_LEFT:
        print('Left Side.')
        return
    if data[AC_STATE_INDEX] == AC_UP:
        print('Up Side.')
        return
    if data[AC_STATE_INDEX] == AC_RIGHT:
        print('Right Side.')
        return
    if data[AC_STATE_INDEX] == AC_FRONT:
        print('Front Side.')
        return
    if data[AC_STATE_INDEX] == AC_BACK:
        print('Back Side.')
        return

def on_receive_indicate(sender, data: bytearray):
    data = bytes(data)
    print('[indicate] ',data)

async def scan(prefix):
    while True:
        print('scan...')
        try:
            return next(d for d in await discover() if d.name and d.name.startswith(prefix))
        except StopIteration:
            continue

async def main():
    # Scan device
    device_BU = await scan(SN_BU)
    print('found', device_BU.name)
    async with BleakClient(device_BU, timeout=None) as client:
        await client.start_notify(CORE_NOTIFY_UUID, on_BU_receive_notify)
        await client.start_notify(CORE_INDICATE_UUID, on_receive_indicate)
        await client.write_gatt_char(CORE_WRITE_UUID, pack('<BBBB', 0, 2, 1, 3), response=True)
        print('connected')
        await asyncio.sleep(30)

    device_AC = await scan(SN_AC)
    print('found', device_AC.name)
    async with BleakClient(device_AC, timeout=None) as client:
        await client.start_notify(CORE_NOTIFY_UUID, on_AC_receive_notify)
        await client.start_notify(CORE_INDICATE_UUID, on_receive_indicate)
        await client.write_gatt_char(CORE_WRITE_UUID, pack('<BBBB', 0, 2, 1, 3), response=True)
        print('connected')

        await asyncio.sleep(30)
    
# Initialize event loop
if __name__ == '__main__':
    loop = asyncio.get_event_loop()
    loop.run_until_complete(main())