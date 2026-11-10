# ad5664 — A&D AD-5664 ワイヤレス温湿度計の SDR デコーダ（非公式）

A&D の熱中症指数モニター **AD-5664SET** の子機（AD-5664-01）が送っている 429 MHz 帯の電波を、RTL-SDR で受信して温度・湿度・WBGT を読み取るための資料と Python ライブラリです。

- 📄 **プロトコルの解説**: [docs/protocol.md](docs/protocol.md)
  変調方式、パケットの構造、チェックサムのほか、何が確定していて何が未解明かをまとめています。
- 🐍 **Python ライブラリ**: パケットの解析部分は依存ライブラリなし。I/Q の復調には numpy を使います。
- 💻 **コマンドラインツール**: `ad5664 listen` で受信した値を表示します。JSON や CSV でも出力できます。

> **非公式です。** 株式会社エー・アンド・デイとは関係ありません。製品名は、対象の機器を説明するためだけに使っています。内容は、自分の機器の電波を受信して解析した結果に基づく推定です。正しさは保証しません。

## English summary

Unofficial decoder for the **A&D AD-5664-01** wireless thermo-hygrometer sensor (sold in Japan as part of the AD-5664SET heat-stroke index monitor).
Signal: 429.20 MHz, 2-FSK (±11 kHz), 10 kbit/s NRZ, preamble `AA…`, sync word `2D D4`, 17-byte packet sent 3 times, every 121–129 s depending on the channel.
The last byte is a simple sum checksum. Each packet carries the channel, a random ID, humidity, temperature, WBGT, volumetric humidity and the time until the next transmission.
See [docs/protocol.md](docs/protocol.md) (Japanese) and [`src/ad5664/packet.py`](src/ad5664/packet.py) (English comments) for the field layout.

## わかっていること（概要）

| 項目 | 値 |
|---|---|
| 周波数 | 429.20 MHz（CH1〜5 で共通） |
| 変調 | 2値 FSK、偏移 約 ±11 kHz、10 kbps、NRZ |
| フレーム | プリアンブル `AA…` → 同期語 `2D D4` → 17 バイト（同じものを 3 回送信） |
| 中身 | CH・ID、湿度、温度、WBGT、VH（容積絶対湿度）、次の送信までの時間、チェックサム |
| 送信間隔 | CH1 121 秒 〜 CH5 129 秒（2 秒きざみ）。電池を入れた直後の 2 分間は約 15 秒ごと |

パケットの例 `2A F7 00 32 12 03 00 00 00 00 EC 00 8D 00 C7 03 AB` の読み方:

| バイト | 0–1 | 2 | 3 | 4–5 | 6–8 | 9–10 | 11–12 | 13 | 14–15 | 16 |
|---|---|---|---|---|---|---|---|---|---|---|
| 値 | `2A F7` | `00` | `32` | `12 03` | `00 00 00` | `00 EC` | `00 8D` | `00` | `C7 03` | `AB` |
| 意味 | CH1・ID | フラグ | 湿度 50 % | 温度 28.6 ℃ | 不明 | WBGT 23.6 | VH 14.1 | 不明 | 次の送信まで 121 秒 | チェックサム |

## 必要なもの

- RTL-SDR のドングル（動作を確かめたのは、チューナーが FC0013 の HTOOL SDR V2 だけです）
- `rtl_sdr` コマンド（[rtl-sdr](https://github.com/osmocom/rtl-sdr) に含まれています。Debian/Ubuntu なら `sudo apt install rtl-sdr`）
- Python 3.8 以上と numpy

解析と長時間の受信は、Orange Pi 4 Pro（Ubuntu）で行いました。テストは Windows の Python 3.11 で通ることを確かめています。

## インストール

```bash
git clone https://github.com/yas0ka/ad5664-sdr.git
cd ad5664-sdr
pip install .
```

## 使い方

### コマンドライン

```bash
ad5664 listen                      # 受信して 1 行ずつ表示（Ctrl+C で終了）
ad5664 listen --format json        # JSON Lines で出力
ad5664 listen --format csv > log.csv
ad5664 decode capture.cu8          # rtl_sdr で保存したファイル（2.4 MS/s）を解読
ad5664 parse 2A F7 00 32 12 03 00 00 00 00 EC 00 8D 00 C7 03 AB
```

出力の例:

```
2026-11-10 20:36:12  CH1   21.6 C   46 %  WBGT 17.2  VH  8.8  ID 201B
2026-11-10 20:36:13  CH2   22.4 C   44 %  WBGT 17.7  VH  8.8  ID 48E1
```

受信できないときは、次を試してください。

- 周波数を変える（`-f 429.2e6` など）。チューナーによっては、信号が 1 MHz 近くずれて見えます（[docs/protocol.md](docs/protocol.md) 2.1 節）。プログラムは受信した幅の中から信号を自動で探すので、受信幅（±1.2 MHz）の中に入っていれば受信できます。
- 子機の電池を入れ直す。直後の 2 分間は 15 秒ごとに送信するので、試しやすくなります。

### ライブラリ

```python
import ad5664

for rx in ad5664.listen():          # rtl_sdr を起動して受信を続ける
    r = rx.reading
    print(r.channel, r.temperature, r.humidity, r.wbgt)
```

```python
import ad5664

r = ad5664.parse_hex('2A F7 00 32 12 03 00 00 00 00 EC 00 8D 00 C7 03 AB')
print(r.temperature)                # 28.6
```

| 関数・クラス | 内容 | numpy |
|---|---|---|
| `ad5664.parse(bytes)` / `parse_hex(str)` | 17 バイトから `Reading` を作る。チェックサムが合わなければ `ValueError` | 不要 |
| `ad5664.verify(bytes)` | チェックサムが合っているかを返す | 不要 |
| `ad5664.listen(freq, fs, gain, device, ppm)` | `rtl_sdr` を起動し、`Reception` を次々に返す | 必要 |
| `ad5664.read_file(path, fs)` | 保存した `.cu8` ファイルから `Reception` を返す | 必要 |
| `ad5664.demod.decode_burst(iq, fs)` | 1 回の送信分の I/Q から、正しいパケットを取り出す | 必要 |
| `ad5664.demod.BurstDetector` | 連続した I/Q の中から送信区間を見つける | 必要 |

`Reading` には `channel`、`sensor_id`、`humidity`、`temperature`、`wbgt`、`vh`、`next_tx_s`、`fast_mode`、`raw` があります。`Reception` には、`reading` に加えて受信時刻 `time`、くり返しのうち一致した数 `repeats`、周波数のずれ `offset_hz` があります。

子機を見分けるときは、`sensor_id` ではなく `channel` を使ってください。ID は電池を交換するたびに変わります。

### ほかの受信機で使う

パケットの解析部分（`ad5664.packet`）は純粋な Python なので、CC1101 や RFM69 などの受信モジュールで受けた 17 バイトにもそのまま使えます（[examples/parse_bytes.py](examples/parse_bytes.py)）。ただし、受信モジュールでは試していません。

### サンプル

- [examples/print_readings.py](examples/print_readings.py): 受信した値を表示する
- [examples/log_sqlite.py](examples/log_sqlite.py): SQLite に記録する
- [examples/parse_bytes.py](examples/parse_bytes.py): バイト列を解析する

## テスト

```bash
pip install -e ".[test]"
pytest
```

テストでは、実際に受信した 40 種類のパケット（[tests/data/packets.txt](tests/data/packets.txt)）と、合成した FSK 信号を使います。

## 電波法について

自分の機器が出す電波を受信して、自分で使う分には問題ありません。ただし、他人の機器の電波から得た内容を他人に漏らしたり、不正に使ったりすることは、電波法第 59 条（秘密の保護）で禁止されています。このリポジトリのデータは、すべて作者自身の子機から受信したものです。

## 協力のお願い

未解明の点があります（[docs/protocol.md](docs/protocol.md) 6 節）。特に、次の情報があると助かります。

- 0 ℃ より下の温度のときのパケット
- 温度プローブ（T1・T2）をつないだときのパケット
- 電池残量が少ないときのパケット
- FC0013 以外のチューナーで、信号がどの周波数に見えたか

Issue や Pull Request で教えてください。

## ライセンス

MIT License（[LICENSE](LICENSE)）
