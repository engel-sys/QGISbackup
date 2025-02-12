import datetime
import json
import math
import os
import requests
import time
from qgis.PyQt.QtWidgets import QDialog, QVBoxLayout, QLabel, QLineEdit, QComboBox, QPushButton, QProgressBar
from PyQt5.QtCore import Qt
from qgis.core import QgsVectorLayer, QgsProject, Qgis
from qgis.utils import iface

def latlon_to_tile(lat, lon, zoom):
    """緯度経度をズームレベルに基づいてタイル座標に変換する"""
    lat_rad = math.radians(lat)
    n = 2.0 ** zoom
    xtile = int((lon + 180.0) / 360.0 * n)
    ytile = int((1.0 - math.log(math.tan(lat_rad) + 1 / math.cos(lat_rad)) / math.pi) / 2.0 * n)
    return xtile, ytile

# ユーザーへの通知
def usermessage(message, nonerror):
    # エラーメッセージでない場合
    if nonerror:
        iface.messageBar().pushMessage(message)
    else:
        iface.messageBar().pushMessage(message, level=Qgis.Critical)


# QComboBoxの操作
class CustomComboBox(QComboBox):
    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Return or event.key() == Qt.Key_Enter:
            self.showPopup()
        else:
            super().keyPressEvent(event)

# インプットフィールドの作成
class InputDialog(QDialog):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("項目入力")
        self.layout = QVBoxLayout()

        # 必須項目;座標データ
        self.coord_input = self._create_input_field(
            "座標 (緯度, 経度) [必須]",
            "座標を入力 (例: 35.6895, 139.6917)"
        )

        # 必須項目: ズームレベル入力
        self.zoom_input = self._create_input_field(
            "ズームレベル [必須、11～15、大きい数字程ズームされます]",
            "ズームレベルを入力 (例: 15)"
        )

        # 必須項目: 取引時期 from
        self.start_input = self._create_input_field(
            "取引時期 from (四半期) [必須]",
            "（例：20241）"
        )

        # 必須項目: 取引時期 to
        self.end_input = self._create_input_field(
            "取引時期 to (四半期) [必須]",
            "（例：20244）"
        )

        self.layer_and_filename = self._create_input_field(
            "ファイルおよびレイヤー名を入力[必須]",
            "名前を入力"
        )

        # 任意項目: 価格情報区分
        self.classify = self._create_combo_box(
            "価格情報区分 [任意]",
            ["選択なし", "不動産取引価格情報のみ", "成約価格情報のみ"]
        )

        # 任意項目: 土地区分
        self.landtype = self._create_combo_box(
            "土地区分 [任意]",
            ["選択なし", "宅地（土地のみ）", "宅地（土地と建物）", "中古マンションなど", "農地", "林地"]
        )

        # 結果表示用ラベル
        self.result_label = QLabel("Tabキーでカーソル移動、Enterでリストを開く")
        self.layout.addWidget(self.result_label)

        # 実行ボタンを追加
        self.execute_button = QPushButton("実行")
        self.execute_button.clicked.connect(self.convert)
        self.layout.addWidget(self.execute_button)

        # レイアウト適用
        self.setLayout(self.layout)
        self.input_values = None

    def _create_input_field(self, label_text, placeholder_text):
        """必須入力フィールドを作成"""
        self.layout.addWidget(QLabel(label_text))
        input_field = QLineEdit()
        input_field.setPlaceholderText(placeholder_text)
        self.layout.addWidget(input_field)
        return input_field


    def _create_combo_box(self, label_text, items):
        """任意のセレクトボックスを作成"""
        self.layout.addWidget(QLabel(label_text))
        combo_box = CustomComboBox()  # QComboBoxの代わりにCustomComboBoxを使用
        combo_box.setFocusPolicy(Qt.StrongFocus)
        combo_box.addItems(items)
        self.layout.addWidget(combo_box)
        return combo_box


    def convert(self):
        """入力値を検証し、タイル座標を計算"""
        try:
            # 必須項目のバリデーション
            coord_text = self.coord_input.text().strip()
            if not coord_text or "," not in coord_text:
                usermessage("緯度・経度は、半角数字で入力してください。", False)
                return  # エラーが発生した場合はここで処理を終える

            lat, lon = map(float, coord_text.split(","))

            zoom_text = self.zoom_input.text().strip()
            if not zoom_text.isdigit():
                usermessage("ズームレベルは、半角の整数で入力してください。", False)
                return  # エラーが発生した場合はここで処理を終える

            zoom = int(zoom_text)
            if not (11 <= zoom <= 15):
                usermessage("11から15の範囲で入力してください。", False)
                return  # エラーが発生した場合はここで処理を終える

            start_quarter = self.start_input.text().strip()
            end_quarter = self.end_input.text().strip()
            if not start_quarter.isdigit() or not end_quarter.isdigit():
                usermessage("取引時期は、整数で入力してください", False)
                return  # エラーが発生した場合はここで処理を終える

            if not self.layer_and_filename.text():
                usermessage("ファイルおよびレイヤー名を入力してください", False)
                return  # エラーが発生した場合はここで処理を終える

            # タイル座標計算
            xtile, ytile = latlon_to_tile(lat, lon, zoom)

            classifycodedict = {"選択なし": None,
                                "不動産取引価格情報のみ": "01",
                                "成約価格情報のみ": "02"
                                }

            landtypedict = {
                "選択なし": None,
                "宅地（土地のみ）": "01",
                "宅地（土地と建物）": "02",
                "中古マンションなど": "07",
                "農地": "10",
                "林地": "11"
            }

            # 結果の保存
            self.input_values = {
                "response_format": "geojson",
                "z": zoom,
                "x": xtile,
                "y": ytile,
                "from": start_quarter,
                "to": end_quarter,
                "priceClassification": classifycodedict[self.classify.currentText()],
                "landTypeCode": landtypedict[self.landtype.currentText()]
            }
            self.accept()  # 全ての検証をパスした場合のみ、ダイアログを閉じる

        except Exception as e:
            self.input_values = None
            usermessage(f"エラーが発生しました: {e}",False)

    def get_input_values(self):
        """入力された値を取得"""
        return self.input_values

    def get_layer_and_filename(self):
        return self.layer_and_filename.text()

dialog = InputDialog()
if dialog.exec_() == QDialog.Accepted:  # モーダルで表示し、ユーザーが「実行」を押したか確認
    # InputDialog内部のget_input_values関数を呼び出し
    price_input_values = dialog.get_input_values()
    if price_input_values is None:
        usermessage("入力値が正しくありません。", False)
    else:
        # APIキー(環境変数から取得)およびAPIリクエスト作成
        reinfolibkey = os.getenv("reinfolibkey")
        price_url = "https://www.reinfolib.mlit.go.jp/ex-api/external/XPT001?"
        header = {"Ocp-Apim-Subscription-Key": reinfolibkey}
        response = requests.get(price_url, params=price_input_values, headers=header)
        # カラム名の日本語対応辞書
        column_names_ja = {
            'price_information_cagegory_name_ja': '価格情報区分',
            'district_code': '地区コード',
            'city_code': '市区町村コード',
            'prefecture_name_ja': '都道府県名',
            'city_name_ja': '市区町村名',
            'district_name_ja': '地区名',
            'u_transaction_price_total_ja': '取引価格（総額）',
            'u_unit_price_per_tsubo_ja': '坪単価',
            'floor_plan_name_ja': '間取り',
            'u_area_ja': '面積',
            'u_transaction_price_unit_price_square_meter_ja': '取引価格（平方メートル単価）',
            'land_shape_name_ja': '土地の形状',
            'u_land_frontage_ja': '間口',
            'u_building_total_floor_area_ja': '建物の延床面積',
            'u_construction_year_ja': '建築年',
            'building_structure_name_ja': '建物の構造',
            'land_use_name_ja': '用途地域',
            'future_use_purpose_name_ja': '今後の利用目的',
            'front_road_azimuth_name_ja': '前面道路の方位',
            'front_road_type_name_ja': '前面道路の種類',
            'u_front_road_width_ja': '前面道路の幅員',
            'u_building_coverage_ratio_ja': '建蔽率',
            'u_floor_area_ratio_ja': '容積率',
            'point_in_time_name_ja': '取引時点',
            'remark_renovation_name_ja': '改装',
            'transaction_contents_name_ja': '取引の事情等'
        }
        if response.status_code == 200:
            # 一時ファイルにgeojsonデータを保存
            # ファイル名にタイムスタンプを追加
            timestamp = datetime.datetime.now().strftime("%Y%m%d%H%M%S")
            layer_and_filename = dialog.get_layer_and_filename()
            temp_file = f"{layer_and_filename}_{timestamp}.geojson"
            response_dict = response.json()
            if response_dict["features"]:
                try:
                    with open(temp_file, 'wb') as f:
                        try:
                            f.write(response.content)
                            usermessage("一時ファイルへの書き込みが成功しました", True)
                        except Exception as e:
                            usermessage(f"一時ファイルへの書き込みが失敗しました:{e}", False)


                    def rename_geojson_columns(geojson_file_path):
                        # GeoJSONファイルを読み込む
                        with open(geojson_file_path, 'r', encoding='utf-8') as f:
                            data = json.load(f)
                        # featuresの各プロパティの名前を変更
                        for feature in data['features']:
                            properties = feature['properties']
                            new_properties = {}
                            for old_name, value in properties.items():
                                if old_name in column_names_ja:
                                    new_properties[column_names_ja[old_name]] = value
                                else:
                                    new_properties[old_name] = value
                            feature['properties'] = new_properties

                        # 変更したデータを同じファイルに書き戻す
                        with open(geojson_file_path, 'w', encoding='utf-8') as f:
                            json.dump(data, f, ensure_ascii=False, indent=2)

                        return geojson_file_path

                    renamed_file = rename_geojson_columns(temp_file)
                    # GeoJsonファイルをベクターレイヤーとして読み込み
                    try:
                        # エンコーディングを設定
                        options = QgsVectorLayer.LayerOptions()
                        options.encoding = "UTF-8"
                        layer = QgsVectorLayer(renamed_file, layer_and_filename, "ogr", options)
                        usermessage("一時ファイルへの読み込みが成功しました", True)
                    except Exception as e:
                        usermessage(f"一時ファイルへの読み込みが失敗しました:{e}", False)

                    if layer.isValid():
                        # レイヤーをQGISプロジェクトに追加
                        QgsProject.instance().addMapLayer(layer)
                        usermessage("レイヤーが正常に追加されました", True)
                        layer = None  # レイヤーを解放
                        time.sleep(1)  # ファイル解放の待機
                    else:
                        usermessage("レイヤーが無効です", False)

                except Exception as e:
                    usermessage(f"エラーが発生しました: {e}", False)
            else:
                usermessage("取引事例が見つかりません", False)
        else:
            usermessage(f"APIリクエストエラー:{response.status_code}", False)
