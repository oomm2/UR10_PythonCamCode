"""Validated, atomic local settings; UI labels are not persisted identifiers."""
import json
import math
import os
import tempfile
from safety_config import URSIM_TARGET, REAL_TARGET
from .vision import MANUAL_CAMERA

class SettingsMixin:
    def _settings_data(self):
        return {
            'target': self.target_combo.currentText(),
            'robot_ip_ursim': self._ips.get(URSIM_TARGET, '127.0.0.1'),
            'robot_ip_real': self._ips.get(REAL_TARGET, ''),
            'direction_inverted': self.direction_inverted,
            'depth_inverted': self.depth_inverted,
            'theme': self.theme,
            'camera_source': self.camera_combo.currentData(),
            'camera_manual': self.camera_source.text(),
            **{key:self._value(name) for key,name in (
                ('max_speed','Max speed'),('detection','Detection'),('tracking','Tracking'),
                ('smoothing','Smoothing'),('dead_zone','Dead zone'),('size_sensitivity','Size sensitivity'))}}

    def _load_settings(self):
        self._ips = {URSIM_TARGET:'127.0.0.1', REAL_TARGET:''}
        self._last_target = URSIM_TARGET
        try:
            with open(self.SETTINGS_PATH, encoding='utf-8') as fh:
                data = json.load(fh)
            if not isinstance(data, dict):
                raise ValueError('設定根節點必須為 object')
        except FileNotFoundError:
            return
        except (OSError, ValueError) as exc:
            self.log(f'設定未載入，使用預設值：{exc}')
            return
        self._loading_settings = True
        try:
            theme = data.get('theme', 'light')
            self.theme = theme if theme in ('light', 'dark') else 'light'
            self.theme_button.setText('淺色模式' if self.theme == 'dark' else '深色模式')
            self._apply_style()
            for target, key in ((URSIM_TARGET,'robot_ip_ursim'),(REAL_TARGET,'robot_ip_real')):
                value = data.get(key)
                if isinstance(value, str):
                    self._ips[target] = value.strip()
            target = data.get('target', URSIM_TARGET)
            if target not in (URSIM_TARGET, REAL_TARGET):
                target = URSIM_TARGET
            self.target_combo.blockSignals(True)
            self.target_combo.setCurrentText(target)
            self.target_combo.blockSignals(False)
            self._last_target = target
            self._apply_target_profile(target, data.get('max_speed'))
            self.robot_ip.setText(self._ips[target])
            self.direction_inverted = data.get('direction_inverted') is True
            self.depth_inverted = data.get('depth_inverted') is True
            self.invert_button.setText('移動方向：' + ('反轉' if self.direction_inverted else '正常'))
            self.depth_button.setText('深度方向：' + ('反轉' if self.depth_inverted else '正常'))
            source = data.get('camera_source', '0')
            if isinstance(source, int):
                source = str(source)
            manual = data.get('camera_manual', '')
            if not isinstance(manual,str):
                manual = ''
            if isinstance(source,str) and source.isdigit():
                if self.camera_combo.findData(source) < 0:
                    self.camera_combo.addItem(source,source)
                self.camera_combo.setCurrentIndex(self.camera_combo.findData(source))
            else:
                self.camera_combo.setCurrentIndex(self.camera_combo.findData(MANUAL_CAMERA))
                if source not in (MANUAL_CAMERA,'<Manual entry>','<手動輸入>') and isinstance(source,str):
                    manual = source
            self.camera_source.setText(manual)
            for key,name in (('detection','Detection'),('tracking','Tracking'),('smoothing','Smoothing'),
                             ('dead_zone','Dead zone'),('size_sensitivity','Size sensitivity')):
                try:
                    value = float(data[key])
                    if not math.isfinite(value):
                        raise ValueError()
                    slider, _, scale = self.tuners[name]
                    slider.setValue(round(value * scale))
                except (KeyError, ValueError, TypeError, OverflowError):
                    continue
        finally:
            self._loading_settings = False
        self.log('本機設定已載入')

    def _save_settings(self):
        if not hasattr(self,'_ips') or self._loading_settings:
            return
        self._ips[self.target_combo.currentText()] = self.robot_ip.text().strip()
        temporary = None
        try:
            with tempfile.NamedTemporaryFile('w', dir=os.path.dirname(self.SETTINGS_PATH),
                    prefix='.settings-', suffix='.tmp', encoding='utf-8', delete=False) as fh:
                temporary = fh.name
                json.dump(self._settings_data(), fh, ensure_ascii=False, indent=2, allow_nan=False)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(temporary, self.SETTINGS_PATH)
        except (OSError, ValueError) as exc:
            self.log(f'設定儲存失敗：{exc}')
        finally:
            if temporary and os.path.exists(temporary):
                os.unlink(temporary)
