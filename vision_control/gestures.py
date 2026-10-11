"""Pure calibrated gesture mapping; handedness score is never a motion gate."""

def zone_command(x, y, size, neutral_x, neutral_y, neutral_size, speed,
                 dead_zone=.10, sensitivity=3., depth_inverted=False):
    if neutral_x is None or neutral_y is None or not neutral_size:
        return 0., 0., 0., 'STOP'
    if x < neutral_x - dead_zone:
        return -speed, 0., 0., 'LEFT'
    if x > neutral_x + dead_zone:
        return speed, 0., 0., 'RIGHT'
    relative_depth = (size - neutral_size) / neutral_size
    depth_excess = abs(relative_depth) - .06
    vertical_offset = y - neutral_y
    vertical_excess = abs(vertical_offset) - dead_zone
    if abs(vertical_offset) >= 2 * dead_zone:
        return (0., 0., speed, 'UP') if vertical_offset < 0 else (0., 0., -speed, 'DOWN')
    if depth_excess > 0 and depth_excess / .06 >= vertical_excess / dead_zone:
        vy = max(-speed, min(speed, relative_depth * sensitivity * speed))
        if depth_inverted:
            vy = -vy
        return 0., vy, 0., 'FWD' if vy > 0 else 'BACK'
    if vertical_excess > 0:
        return (0., 0., speed, 'UP') if vertical_offset < 0 else (0., 0., -speed, 'DOWN')
    return 0., 0., 0., 'STOP'
