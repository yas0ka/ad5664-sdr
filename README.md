# ad5664 — A&D AD-5664 ワイヤレス温湿度計の SDR デコーダ（非公式）

A&D の熱中症指数モニター **AD-5664SET** の子機（AD-5664-01）が送っている 429 MHz 帯の電波を、RTL-SDR で受信して温度・湿度・WBGT を読み取るための資料と Python ライブラリです。

- 📄 **プロトコルの解説**: [docs/protocol.md](docs/protocol.md)
  変調方式、パケットの構造、チェックサムのほか、何が確定していて何が未解明かをまとめています。
- 🐍 **Python ライブラリ**: パケットの解析部分は依存ライブラリなし。I/Q の復調には numpy を使います。
- 💻 **コマンドラインツール**: `ad5664 listen` で受信した値を表示します。JSON や CSV でも出力できます。

**0.2.0（2026-11-17）**: 実際の設置場所で長く動かした結果をもとに、受信できないことがある問題を直し、受信感度を大きく上げました（雑音を足した試験で約 16 dB）。ゲインも自動で選ぶようになりました。くわしくは [CHANGELOG.md](CHANGELOG.md) を見てください。

> **非公式です。** 株式会社エー・アンド・デイとは関係ありません。製品名は、対象の機器を説明するためだけに使っています。内容は、自分の機器の電波を受信して解析した結果に基づく推定です。正しさは保証しません。

## English summary

Unofficial decoder for the **A&D AD-5664-01** wireless thermo-hygrometer sensor (sold in Japan as part of the AD-5664SET heat-stroke index monitor).
Signal: 429.20 MHz, 2-FSK (±11 kHz), 10 kbit/s NRZ, preamble `AA…`, sync word `2D D4`, 17-byte packet sent 3 times, every 121–129 s depending on the channel.
The last byte is a simple sum checksum. Each packet carries the channel, a random ID, humidity, temperature, WBGT, volumetric humidity and the time until the next transmission.
See [docs/protocol.md](docs/protocol.md) (Japanese) and [`src/ad5664/packet.py`](src/ad5664/packet.py) (English comments) for the field layout.
Version 0.2.0 adds a weak-signal (soft-decision) decoder, a narrow-band burst detector and automatic gain selection; `ad5664 scan` shows where the signal appears with tuners other than the FC0013.

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
ad5664 listen -v                   # ゲインの選択と、10 分ごとの受信の状態も表示する
ad5664 listen -g 19.7              # ゲインを固定する
ad5664 listen --format json        # JSON Lines で出力
ad5664 listen --format csv > log.csv
ad5664 scan                        # 信号がどこに見えるかを調べる（FC0013 以外のチューナー向け）
ad5664 decode capture.cu8          # rtl_sdr で保存したファイル（2.4 MS/s）を解読
ad5664 parse 2A F7 00 32 12 03 00 00 00 00 EC 00 8D 00 C7 03 AB
```

出力の例（`x3` は 3 回のくり返しのうち一致した数、`dB` は電波の強さ）:

```
2026-11-17 22:31:37  CH4   18.7 C   62 %  WBGT 15.9  VH  9.9  ID 8819  x3   36 dB
2026-11-17 22:33:44  CH4   18.7 C   62 %  WBGT 15.9  VH  9.9  ID 8819  x3   34 dB
```

`-v` を付けたときのゲインの選択の様子（子機 1 台のとき）:

```
22:25:46 gain scan (start): trying 6.5 / -6.3 / 19.7 dB for 140 s each
22:28:06 gain 6.5: CH4(repeats=3, 29dB)
22:30:26 gain -6.3: CH4(repeats=3, 12dB)
22:32:46 gain 19.7: CH4(repeats=3, 36dB)
22:32:46 gain fixed at 19.7 dB (CH4)
```

#### ゲイン

`-g` を指定しないと、ゲインを自動で選びます。6.5 / −6.3 / 19.7 dB を 140 秒ずつ試し（子機の送信間隔は最長 129 秒）、受信できた子機の数、3 回のくり返しの一致数、いちばん弱い子機の電波の強さ、の順に比べていちばんよいものに固定します。選んでいる間（約 7 分）も、受信した値は表示します。固定したあとで、受信できていた子機が 10 分受信できなくなると、選び直します。

ゲイン自動（AGC）は使いません。AGC では雑音の大きさが起動のたびに大きく変わり、信号が振り切れることもあったためです。候補は `--gains` で変えられます（`rtl_sdr` は、指定した値にいちばん近い、チューナーが対応しているゲインを使います）。

#### 受信できないとき

- **周波数**: 信号は `-f` で指定した周波数から ±50 kHz 以内に見えている必要があります。動作を確かめた FC0013 のドングルでは、既定の `-f 429.9e6` でちょうど中心に見えます。ほかのチューナー（R820T など）では 429.20 MHz 付近に見えるはずですが、確かめていません。`ad5664 scan` を実行すると、受信幅（±1.2 MHz）の中で信号がどこに見えたかを表示するので、表示された `--offset` を `listen` に付けてください。

  ```
  $ ad5664 scan
  CH4  offset     +6.2 kHz  (429.9062 MHz)   18.9 C   61 %

  Found CH4 near the centre (+6.2 kHz). No --offset is needed:
    ad5664 listen -f 429900000
  ```

  中心から 20 kHz 以上ずれていれば、`--offset` を付けた `listen` のコマンドを表示します。

  `scan` は強い電波しか見つけられないので、子機をアンテナの近くに置いてください。
- **子機の電池を入れ直す**: 直後の 2 分間は 15 秒ごとに送信するので、試しやすくなります。
- **アンテナ**: 付属のホイップのような 1/4 波長（429 MHz で約 17.5 cm）のアンテナは、下に金属の板（地板）がないと性能が出ません。金属の上に置くか、直径 30 cm くらいの金属板を敷いてください。

### ライブラリ

```python
import ad5664

for rx in ad5664.listen():          # rtl_sdr を起動して受信を続ける（ゲインは自動で選ぶ）
    r = rx.reading
    print(r.channel, r.temperature, r.humidity, r.wbgt, rx.snr_db)
```

ゲインの選択の様子は `logging` の `ad5664` ロガーに出ます（`logging.basicConfig(level=logging.INFO)` で表示されます）。

```python
import ad5664

r = ad5664.parse_hex('2A F7 00 32 12 03 00 00 00 00 EC 00 8D 00 C7 03 AB')
print(r.temperature)                # 28.6
```

| 関数・クラス | 内容 | numpy |
|---|---|---|
| `ad5664.parse(bytes)` / `parse_hex(str)` | 17 バイトから `Reading` を作る。チェックサムが合わなければ `ValueError` | 不要 |
| `ad5664.verify(bytes)` | チェックサムが合っているかを返す | 不要 |
| `ad5664.plausible(bytes)` | チェックサムに加えて、中身のつじつまが合うか（byte 2、値の範囲、VH と温度・湿度の関係）を返す | 不要 |
| `ad5664.listen(freq, fs, gain, ..., offset_hz, gains)` | `rtl_sdr` を起動し、`Reception` を次々に返す。`gain=None` ならゲインを自動で選ぶ | 必要 |
| `ad5664.scan(freq, fs, gain, seconds)` | 受信幅全体から信号を探す。結果の `offset_hz` を `listen` に渡す | 必要 |
| `ad5664.read_file(path, fs, offset_hz)` | 保存した `.cu8`（`.cu8.gz`）ファイルから `Reception` を返す | 必要 |
| `ad5664.demod.decode_burst(iq, fs)` | 1 回の送信分の I/Q から、正しいパケットを取り出す | 必要 |
| `ad5664.demod.BurstFinder(fs, offset_hz)` | 連続した I/Q の中から送信区間を見つける | 必要 |
| `ad5664.GainSelector` | ゲインの自動選択の判断だけを行う（自分で受信の仕組みを作るとき用） | 不要 |

`Reading` には `channel`、`sensor_id`、`humidity`、`temperature`、`wbgt`、`vh`、`next_tx_s`、`fast_mode`、`raw` があります。`Reception` には、`reading` に加えて受信時刻 `time`、くり返しのうち一致した数 `repeats`、周波数のずれ `offset_hz`、電波の強さ `snr_db`（検出に使う ±50 kHz の帯域で、雑音の床との比）、使ったゲイン `gain` があります。

`repeats` が 1 のときは、3 回のくり返しを足し合わせてやっと読めた、弱い電波です（値は中身の検査を通っています）。

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

テストでは、次のデータを使います。

- 実際に受信した 41 種類のパケット（[tests/data/packets.txt](tests/data/packets.txt)）
- 実際に記録した 1 回分の送信（[tests/data/ch1_burst.cu8](tests/data/ch1_burst.cu8)、`rtl_sdr -f 429900000 -s 2400000` で記録した 0.13 秒分）
- 低いゲイン（6.5 dB）で記録した 0.8 秒分（[tests/data/ch5_lowgain.cu8.gz](tests/data/ch5_lowgain.cu8.gz)）。基板からの周期的な雑音が入っていて、0.1.0 では何も見つけられなかったもの
- 上の記録に雑音を足した、弱い電波
- 合成した FSK 信号

### 受信感度の測り方

```bash
python tools/sensitivity.py                  # 0.2.0
python tools/sensitivity.py --decoder hard   # 解読だけ 0.1.0 の方式にする
```

実際の記録に雑音を足して、どこまで弱い電波を読めるかを数えます。SNR は「信号の電力 ÷ 信号の幅（40 kHz）に入る雑音の電力」です。

| SNR | 0.1.0 | 0.2.0 |
|---:|---:|---:|
| 19 dB | 100 % | 100 % |
| 17 dB | 0 % | 100 % |
| 6 dB | 0 % | 100 % |
| 3 dB | 0 % | 97 % |
| 2 dB | 0 % | 70 % |
| 1 dB | 0 % | 37 % |
| 0 dB | 0 % | 7 % |

半分読める強さは、0.1.0 の約 18 dB から約 1.5 dB になりました（0.1.0 は送信区間を見つける所が限界でした）。どの強さでも、間違った値は 0 件でした。ただし、この試験はドングル固有の直流成分、振り切れ、ほかの電波による妨害を再現していません。

### 実際の電波での確認

- 子機 5 台を同時に動かして 14 分間記録したデータを 0.2.0 で解読し、50 回の送信をすべて正しく読めました（0.1.0 の結果と全件一致、余分な受信は 0 件）。
- 0.2.0 の検出とゲイン選択のもとになった受信プログラムで、子機 5 台（別々の部屋）を 29 時間受信し続けたときの受信率は 97.8〜99.7 % でした。取りこぼしの一部は、子機どうしの送信が重なったためです。
- 遠くに置いた子機の電波を 13 分記録し、同じ記録に雑音を足して、0.2.0 の一つ前の受信プログラム（検出は ±50 kHz の方式、解読は 0.1.0 の方式）と 0.2.0 で比べると、半分読める所が約 9.5 dB 改善しました。
- Orange Pi 4 Pro（Ubuntu、Python 3.14、numpy 2.5）で、1 回の送信の解読にかかる時間は約 0.2 秒です。

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
