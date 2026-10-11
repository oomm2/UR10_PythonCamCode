// Display-only translations. Telemetry values, API payloads and recordings are unchanged.
export const LANGUAGES = ['en', 'yue'];
const STORAGE_KEY = 'ur-monitor-language';

const CANTONESE = {
  'UR Read-only Monitor': 'UR 唯讀監控',
  'UR10 Monitor': 'UR10 監控',
  'Read-only RTDE telemetry · Windows': '唯讀 RTDE 遙測 · Windows',
  'Starting…': '啟動中…', 'Frozen': '已暫停畫面', 'Disconnected': '未連線',
  'Connected': '已連線', 'Connecting': '連線中', 'Waiting': '等候中',
  'Unknown': '未知', 'Active': '運作中', 'Stale': '已過期',
  'Theme': '主題', 'Toggle light/dark theme (T)': '切換深色／淺色主題 (T)',
  'Switch English / Cantonese': '切換英文／廣東話',
  'Keyboard shortcuts (?)': '快捷鍵 (?)',
  'Digital Twin': '數碼孿生', 'Trail': '軌跡', 'Limits': '關節限制', 'Export': '匯出',
  'Show the recorded TCP trail (L)': '顯示已記錄 TCP 軌跡 (L)',
  'Show joint limit rings (J)': '顯示關節限制環 (J)',
  'Export a PNG snapshot or HTML report (S)': '匯出 PNG 截圖或 HTML 報告 (S)',
  'Loading robot URDF…': '載入機械人 URDF 中…',
  'Drag to orbit · Scroll to zoom': '拖動旋轉 · 捲動縮放',
  'R reset · G grid · Space freeze · S export · 1-6 joint':
    'R 重設 · G 格線 · Space 暫停畫面 · S 匯出 · 1-6 關節',
  'Play': '播放', 'Pause': '暫停', 'Rewind': '返回起點', 'Reload': '重新載入',
  'Play/pause the recorded trail (Space)': '播放／暫停軌跡 (Space)',
  'Trajectory position': '軌跡位置', 'Replay speed': '重播速度',
  'Reload the trail from the server': '從伺服器重新載入軌跡',
  ' points': ' 個點', 'Path length ': '路徑長度 ',
  'Robot Status': '機械人狀態', 'Robot IP': '機械人 IP',
  'Windows monitor IP': 'Windows 監控 IP', 'Last update': '最近更新',
  'Robot mode': '機械人模式', 'Safety mode': '安全模式',
  'Speed scaling': '速度比例', 'Monitor role': '監控角色',
  'RTDE output-only': '只讀 RTDE 輸出', 'Data transport': '資料傳輸',
  'Link Diagnostics': '連線診斷', 'Reconnects': '重新連線次數',
  'Cumulative uptime': '累計運作時間', 'Samples read': '已讀樣本',
  'Trail points': '軌跡點數', 'SSE subscribers': 'SSE 訂閱者',
  'Last sample age': '最近樣本時間',
  'No disconnect recorded in this session.': '今次運行未有斷線紀錄。',
  'Latency': '延遲', 'Mode': '模式', 'Last heartbeat': '最近心跳',
  'Mean': '平均', 'Min / Max': '最低／最高', 'Samples': '樣本',
  'Monotonic mode measures arrival delay without needing clock synchronisation with the Mac.':
    'Monotonic 模式量度到達延遲，毋須同 Mac 對時。',
  'Clock-synced mode: heartbeat timestamps are compared directly against this host clock.':
    '對時模式會直接用呢部電腦嘅時鐘比較心跳時間戳記。',
  'Clock-synced mode. Last offset vs Mac clock: ': '對時模式。最近同 Mac 嘅時間偏差：',
  'Last disconnect: ': '最近斷線：',
  'Control Client': '控制端', 'Observed source IP': '觀察到嘅來源 IP',
  'Controller name': '控制端名稱', 'Reported state': '回報狀態',
  'Control protocol': '控制協定', 'Heartbeat age': '心跳時間',
  'Target robot': '目標機械人', 'Vision FPS': '影像 FPS',
  'Tracking confidence': '追蹤信心分數', 'Current gesture': '目前手勢',
  'Dropped frames': '掉幀數', 'Inference time': '推論時間',
  'Heartbeats received': '收到嘅心跳',
  'The controller can send a heartbeat each second. Windows records its HTTP source IP; RTDE does not list other client IPs.':
    '控制端可以每秒發送心跳。Windows 會記錄 HTTP 來源 IP；RTDE 唔會列出其他 client 嘅 IP。',
  'TCP Pose': 'TCP 姿態', 'Position (x, y, z) m': '位置 (x, y, z) 米',
  'Orientation (rx, ry, rz)': '方向 (rx, ry, rz)',
  'Tool speed (linear / angular)': '工具速度（線性／角速度）',
  'Joint Angles': '關節角度', 'Bars show nominal ROS planning ranges.': '長條顯示 ROS 規劃參考範圍。',
  'Joint current (A)': '關節電流 (A)', 'Joint temperature (°C)': '關節溫度 (°C)',
  'Digital I/O': '數碼輸入／輸出',
  'Trends': '趨勢', 'Joint angles (degrees) · last 600 samples': '關節角度（度）· 最近 600 個樣本',
  'Tool speed (m/s) and joint current (A)': '工具速度 (m/s) 同關節電流 (A)',
  'Trends are kept in the browser only and are cleared on reload. Charts do not alter the RTDE session.':
    '趨勢只喺瀏覽器保存，重新載入會清除；圖表唔會改動 RTDE 連線。',
  'Baseline Comparison': '基線比較', 'Left recording': '左邊錄製',
  'Right recording': '右邊錄製', 'Compare': '比較',
  'Left · joint angles (deg)': '左邊 · 關節角度（度）',
  'Right · joint angles (deg)': '右邊 · 關節角度（度）',
  'Pick two recordings to compare Mac control against a manual jog run.':
    '揀兩段錄製，比較 Mac 控制同手動移動。',
  'Keyboard Shortcuts': '快捷鍵',
  'freeze / resume the live view': '暫停／恢復即時畫面',
  'export a PNG snapshot + HTML report': '匯出 PNG 截圖及 HTML 報告',
  'highlight a single joint (press again to clear)': '突出顯示單一關節（再按一次取消）',
  'toggle the recorded trail': '切換已記錄軌跡',
  'toggle joint limit rings': '切換關節限制環',
  'play / pause trajectory replay': '播放／暫停軌跡重播',
  'clear the on-screen trail': '清除畫面上嘅軌跡',
  'switch light / dark theme': '切換淺色／深色主題',
  'reset the 3D view · ': '重設 3D 視角 · ', 'toggle grid': '切換格線',
  'show or hide this panel': '顯示／隱藏呢個面板',
  'Events': '事件', 'Waiting for RTDE telemetry…': '等候 RTDE 遙測資料…',
  // Dynamic status and event messages.
  'Within nominal display range': '喺參考顯示範圍內',
  'Outside nominal display range': '超出參考顯示範圍',
  'Range metadata unavailable; bars disabled. Numeric joint values remain available.':
    '關節範圍資料不可用；長條已停用，數值仍可查看。',
  'Reduced range: ': '縮小嘅參考範圍：',
  'Amber = outside nominal range. Live values and 3D poses are never clamped.':
    '琥珀色代表超出參考範圍；即時數值同 3D 姿態唔會截斷。',
  'Joint range metadata unavailable: ': '關節範圍資料不可用：',
  'Controller heartbeat is stale': '控制端心跳已過期',
  'Control heartbeat: ': '控制端心跳：',
  'No timestamp': '冇時間戳記', 'No data': '冇資料',
  'Monotonic arrival delay': 'Monotonic 到達延遲',
  'Clock-synchronised': '已對時',
  ' rejected': ' 個拒絕',
  'No disconnect recorded': '未有斷線紀錄',
  ' jumped ': ' 跳動 ', ' in one sample': '，只用一個樣本',
  'Joint jump detected: ': '偵測到關節跳動：', ' moved ': ' 移動咗 ',
  'Monitor data resumed': '監控資料已恢復', 'Monitor API recovered': '監控 API 已恢復',
  'RTDE connected': 'RTDE 已連線', 'RTDE disconnected': 'RTDE 已斷線',
  'Monitor returned HTTP ': '監控回傳 HTTP ',
  'Monitor stream disconnected': '監控串流已斷線',
  'Monitor returned an invalid control client object': '監控回傳嘅控制端資料無效',
  'Monitor returned an invalid state object': '監控回傳嘅狀態資料無效',
  'Monitor returned invalid JSON': '監控回傳嘅 JSON 無效',
  'Monitor request timed out': '監控請求逾時',
  'Monitor is unreachable': '無法連到監控',
  'Monitor returned an invalid trajectory object': '監控回傳嘅軌跡資料無效',
  'Monitor trajectory is missing a points array': '監控軌跡欠缺點陣列',
  'Monitor returned an invalid latency object': '監控回傳嘅延遲資料無效',
  'Invalid joint limits': '關節限制資料無效',
  ' model loaded · ': ' 模型已載入 · ',
  'live pose': '即時姿態', 'no live telemetry; pose held': '冇即時遙測；姿態保持不變',
  'Loading ': '載入緊 ', ' model and meshes…': ' 模型同網格…',
  '3D asset load error — telemetry remains available': '3D 資產載入失敗；遙測仍可查看',
  'A robot model asset could not be loaded': '有機械人模型資產無法載入',
  'Heartbeat stale': '心跳已過期', 'controlling': '控制中',
  'Waiting for telemetry…': '等候遙測資料…',
  'View frozen — live updates continue in the background': '畫面已暫停；即時更新仍喺背景繼續',
  'View resumed': '畫面已恢復', 'Trajectory replay finished': '軌跡重播完成',
  'Trajectory loaded: ': '軌跡已載入：', 'Trajectory unavailable: ': '軌跡不可用：',
  ' path': ' 路徑', 'On-screen trail cleared (server-side buffer unchanged)':
    '已清除畫面軌跡；伺服器緩衝區未改動',
  'Two recordings are needed. Record a run first (recording controls live in the API).':
    '要兩段錄製先可以比較；請先錄製（錄製控制喺 API）。',
  'Comparing ': '比較緊 ', ' rows (J1 shown). ': ' 行（顯示 J1）。',
  'Baseline loaded: ': '基線已載入：', 'Baseline comparison failed: ': '基線比較失敗：',
  'Monitor config unavailable: ': '監控設定不可用：',
  '3D snapshot unavailable: ': '3D 截圖不可用：',
  '3D unavailable — telemetry remains available': '3D 不可用；遙測資料仍可查看',
  '3D view unavailable: ': '3D 視角不可用：',
  'Export written: ': '匯出完成：', '3D view reset': '3D 視角已重設',
  'Reference grid ': '參考格線', 'Joint highlight cleared': '已取消突出顯示關節',
  'Highlighting J': '突出顯示 J', 'Trajectory trail ': '軌跡',
  'Joint limit rings ': '關節限制環', 'No trajectory loaded to replay': '未有軌跡可以重播',
  'Theme: dark': '主題：深色', 'Theme: light': '主題：淺色',
  'Reference grid shown': '參考格線已顯示',
  'Reference grid hidden': '參考格線已隱藏',
  'Trajectory trail shown': '軌跡已顯示',
  'Trajectory trail hidden': '軌跡已隱藏',
  'Joint limit rings shown': '關節限制環已顯示',
  'Joint limit rings hidden': '關節限制環已隱藏',
  'Live stream connected': '即時串流已連線',
  'Live stream unavailable; falling back to polling': '即時串流不可用；轉用輪詢',
  'Live stream unsupported; using polling': '唔支援即時串流；轉用輪詢',
  'Monitor offline': '監控離線', 'Monitor timeout': '監控逾時',
  'Monitor HTTP error': '監控 HTTP 錯誤', 'Invalid monitor data': '監控資料無效',
  'Dashboard error': '儀表板錯誤', 'Dashboard update failed: ': '儀表板更新失敗：',
  'NO_CONTROLLER': '冇控制器', 'DISCONNECTED': '已斷線',
  'CONFIRM_SAFETY': '確認安全中', 'BOOTING': '啟動中',
  'POWER_OFF': '電源關閉', 'POWER_ON': '電源開啟',
  'IDLE': '閒置', 'BACKDRIVE': '手動拖動', 'RUNNING': '運行中',
  'UPDATING_FIRMWARE': '更新韌體中', 'PAUSING': '暫停中', 'PAUSED': '已暫停',
  'NORMAL': '正常', 'REDUCED': '降速', 'PROTECTIVE_STOP': '保護停止',
  'RECOVERY': '恢復中', 'SAFEGUARD_STOP': '安全裝置停止',
  'SYSTEM_EMERGENCY_STOP': '系統急停', 'ROBOT_EMERGENCY_STOP': '機械人急停',
  'VIOLATION': '安全違規', 'FAULT': '故障', 'VALIDATE_JOINTS': '驗證關節中',
  'UNDEFINED': '未定義', 'SCALING': '調整比例中', 'PLAIN': '一般',
};

const ordered = Object.entries(CANTONESE).sort((a, b) => b[0].length - a[0].length);

export function isLanguage(value) { return LANGUAGES.includes(value); }
export function nextLanguage(value) { return value === 'en' ? 'yue' : 'en'; }
export function readStoredLanguage(storage) {
  try { const value = storage?.getItem(STORAGE_KEY); return isLanguage(value) ? value : null; }
  catch { return null; }
}
export function translateText(text, language) {
  if (language !== 'yue' || !text) return text;
  let result = text;
  for (const [source, translated] of ordered) result = result.replaceAll(source, translated);
  return result;
}

export function createLanguageController({ doc = document, storage = globalThis.localStorage } = {}) {
  let current = readStoredLanguage(storage) || 'en';
  const textState = new WeakMap();
  const attrState = new WeakMap();
  const attributes = ['title', 'aria-label', 'placeholder'];

  function renderText(node) {
    const previous = textState.get(node);
    const source = previous && node.nodeValue === previous.rendered ? previous.source : node.nodeValue;
    const rendered = translateText(source, current);
    textState.set(node, { source, rendered });
    if (node.nodeValue !== rendered) node.nodeValue = rendered;
  }

  function renderAttributes(element) {
    const state = attrState.get(element) || new Map();
    for (const name of attributes) {
      if (!element.hasAttribute(name)) continue;
      const value = element.getAttribute(name);
      const previous = state.get(name);
      const source = previous && value === previous.rendered ? previous.source : value;
      const rendered = translateText(source, current);
      state.set(name, { source, rendered });
      if (value !== rendered) element.setAttribute(name, rendered);
    }
    attrState.set(element, state);
  }

  function scan(root = doc.body) {
    const walker = doc.createTreeWalker(root, NodeFilter.SHOW_ELEMENT | NodeFilter.SHOW_TEXT);
    for (let node = walker.currentNode; node; node = walker.nextNode()) {
      if (node.nodeType === Node.TEXT_NODE) {
        if (!['SCRIPT', 'STYLE', 'NOSCRIPT'].includes(node.parentElement?.tagName)
            && node.parentElement?.id !== 'language-toggle') renderText(node);
      } else if (node.nodeType === Node.ELEMENT_NODE) {
        renderAttributes(node);
      }
    }
  }

  function processMutations(mutations) {
    for (const mutation of mutations) {
      if (mutation.type === 'characterData') {
        const node = mutation.target;
        if (!['SCRIPT', 'STYLE', 'NOSCRIPT'].includes(node.parentElement?.tagName)
            && node.parentElement?.id !== 'language-toggle') renderText(node);
      } else if (mutation.type === 'attributes') {
        renderAttributes(mutation.target);
      } else {
        for (const node of mutation.addedNodes) scan(node);
      }
    }
  }

  function set(value) {
    current = isLanguage(value) ? value : 'en';
    doc.documentElement.lang = current === 'yue' ? 'zh-HK' : 'en';
    doc.title = translateText('UR Read-only Monitor', current);
    doc.querySelector('#language-toggle').textContent = current === 'en' ? '廣東話' : 'English';
    try { storage?.setItem(STORAGE_KEY, current); } catch { /* private storage may be unavailable */ }
    scan();
    return current;
  }

  set(current);
  const observer = new MutationObserver(processMutations);
  observer.observe(doc.body, { subtree: true, childList: true, characterData: true,
    attributes: true, attributeFilter: attributes });
  return {
    get language() { return current; },
    set, toggle: () => set(nextLanguage(current)),
    disconnect: () => observer.disconnect(),
  };
}
