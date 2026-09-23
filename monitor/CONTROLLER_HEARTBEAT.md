# Controller Heartbeat API

## Why it exists

The Windows monitor uses RTDE **output-only** telemetry. RTDE can report robot state, but it does not tell this monitor which other computer is connected to the URSim controller. Windows cannot see the Mac's direct TCP connection to the VM because the monitor is not a proxy.

For a reliable controller IP, the Mac controller sends a small **token-authenticated heartbeat** to the monitor. The monitor records the TCP source address itself, so the displayed IP is the address Windows observes rather than a user-supplied field. A heartbeat identifies the process that sent it; it does not prove that the process is currently sending robot motion commands. Start it and stop it with the Vision controller lifecycle for an accurate dashboard status.

The heartbeat endpoint never sends anything to the robot and cannot control the robot.

## Token setup

The Windows monitor reads the heartbeat token from the `UR_MONITOR_HEARTBEAT_TOKEN` process environment variable. If that variable is absent, it reads the local ignored `.env` file. `config.json` does not contain the secret anymore. The token is required: if the monitor has no token configured, heartbeat writes fail closed with HTTP 503.

On Windows, copy `.env.example` to `.env` and set a random ASCII token. On the Mac, keep the same value outside source control. For a shell session, read it without displaying the value:

```bash
read -r -s UR_MONITOR_HEARTBEAT_TOKEN
export UR_MONITOR_HEARTBEAT_TOKEN
```

The helper also accepts `--token` for compatibility, but an environment variable is preferred because command-line arguments can be visible to other local processes.

## Endpoint

```text
POST http://192.0.2.20:8080/api/control/heartbeat
Content-Type: application/json
X-Heartbeat-Token: <same token as the Windows monitor>
```

Example JSON:

```json
{
  "name": "Mac Vision controller",
  "state": "controlling",
  "protocol": "Vision / RTDE",
  "session_id": "vision-session-001",
  "robot_ip": "192.0.2.10",
  "details": "hand tracking ready"
}
```

Send this every second while the Mac app is enabled. The dashboard marks it `Active` while it has received a heartbeat in the last 4 seconds, and `Heartbeat stale` afterward.

## Quick test on the Mac

Copy `mac_controller_heartbeat.py` to the Mac, export the token as above, and run:

```bash
python3 mac_controller_heartbeat.py \
  --monitor http://192.0.2.20:8080 \
  --name 'Mac Vision controller' \
  --details 'hand tracking ready'
```

Expected errors are explicit:

- `401` — token is wrong.
- `429` — this source address failed its token check too many times and is temporarily locked out. The response carries a `Retry-After` header in seconds. A correct token always clears the counter.
- `503` — Windows monitor has no token configured and is refusing writes.
- `400` or `413` — malformed or oversized heartbeat payload.
- `403` — a browser request used an origin outside the monitor's exact CORS allowlist. The native Python helper does not send an `Origin` header and is not subject to that browser-only check.

Lockout is per source address and controlled by `heartbeat_max_failures` (default 5) and
`heartbeat_lockout_seconds` (default 60) in `config.json`. Set `heartbeat_lockout_seconds` to `0`
to disable it. Because the counter is per address, a controller that lost network access is never
locked out by another client's failures.

The endpoint is plain HTTP for this private LAN setup. Do not expose it to an untrusted network; use a protected network or add transport security before doing so.

## Swift / Vision integration

Run this on a timer after the Vision controller has started and stop the timer when it stops. Keep the token in runtime configuration, not source control.

```swift
struct ControllerHeartbeat: Encodable {
    let name: String
    let state: String
    let protocolName: String
    let sessionID: String
    let robotIP: String
    let details: String

    enum CodingKeys: String, CodingKey {
        case name, state
        case protocolName = "protocol"
        case sessionID = "session_id"
        case robotIP = "robot_ip"
        case details
    }
}

func sendHeartbeat(token: String, details: String, completion: @escaping (Error?) -> Void) {
    var request = URLRequest(url: URL(string: "http://192.0.2.20:8080/api/control/heartbeat")!)
    request.httpMethod = "POST"
    request.setValue("application/json", forHTTPHeaderField: "Content-Type")
    request.setValue("application/json", forHTTPHeaderField: "Accept")
    request.setValue(token, forHTTPHeaderField: "X-Heartbeat-Token")
    let body = ControllerHeartbeat(
        name: "Mac Vision controller",
        state: "controlling",
        protocolName: "Vision / RTDE",
        sessionID: "vision-session-001",
        robotIP: "192.0.2.10",
        details: details
    )
    request.httpBody = try? JSONEncoder().encode(body)

    URLSession.shared.dataTask(with: request) { _, response, error in
        if let error {
            completion(error)
            return
        }
        guard let http = response as? HTTPURLResponse,
              (200..<300).contains(http.statusCode) else {
            completion(NSError(domain: "ControllerHeartbeat", code: 1))
            return
        }
        completion(nil)
    }.resume()
}
```

If the Mac cannot connect, check that the Windows firewall allows inbound TCP 8080 on the private network and that the monitor is listening on `0.0.0.0`.
