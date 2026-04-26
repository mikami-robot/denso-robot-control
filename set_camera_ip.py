"""
Basler GigE カメラの IP アドレスを 192.168.127.x セグメントに変更するスクリプト
"""
from pypylon import pylon

NEW_IP      = "192.168.127.100"
NEW_SUBNET  = "255.255.255.0"
NEW_GATEWAY = "192.168.127.1"

tlFactory = pylon.TlFactory.GetInstance()

# 接続されている全 GigE デバイスを列挙
devices = tlFactory.EnumerateDevices()

if not devices:
    print("カメラが見つかりません。ケーブルと電源を確認してください。")
    exit(1)

print(f"{len(devices)} 台のカメラを検出しました:\n")
for i, d in enumerate(devices):
    print(f"  [{i}] モデル: {d.GetModelName()}")
    print(f"       現在のIP: {d.GetIpAddress()}")
    print(f"       MAC:      {d.GetMacAddress()}")
    print()

# 対象カメラを選択（1台のみの場合は自動選択）
target = devices[0]
print(f"対象カメラ: {target.GetModelName()} ({target.GetIpAddress()})")
print(f"新しいIP:   {NEW_IP}")
print()

# ForceIP で一時的に IP を設定してから永続 IP を書き込む
cam = pylon.InstantCamera(tlFactory.CreateDevice(target))
cam.Open()

try:
    # 永続 IP を有効化して書き込む
    cam.GetTLNodeMap().GetNode("GevCurrentIPConfigurationPersistentIP").SetValue(True)
    cam.GetTLNodeMap().GetNode("GevPersistentIPAddress").SetValue(NEW_IP)
    cam.GetTLNodeMap().GetNode("GevPersistentSubnetMask").SetValue(NEW_SUBNET)
    cam.GetTLNodeMap().GetNode("GevPersistentDefaultGateway").SetValue(NEW_GATEWAY)

    print("永続IPを書き込みました。カメラを再起動して設定を反映させます...")

    # ソフトウェアリセットで再起動
    cam.GetTLNodeMap().GetNode("DeviceReset").Execute()
    print("カメラをリセットしました。")
    print(f"\n完了: カメラのIPが {NEW_IP} に変更されました。")
    print("10秒ほど待ってから ping 192.168.127.100 で確認してください。")

except Exception as e:
    print(f"エラー: {e}")
    print("\n--- 代替方法: ForceIP ---")
    try:
        tlFactory.ForceIp(target, NEW_IP, NEW_SUBNET, NEW_GATEWAY)
        print(f"ForceIP で {NEW_IP} を一時設定しました（電源OFF/ONで元に戻ります）")
    except Exception as e2:
        print(f"ForceIP も失敗: {e2}")
finally:
    try:
        cam.Close()
    except Exception:
        pass
