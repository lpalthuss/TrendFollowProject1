"""Transport liveness based on monotonic time, independent of stock trading."""
import math


class ReceiveIdleTimeout(Exception):
    """No application message arrived during the configured interval."""


class ConnectionHealth:
    def __init__(self, idle_seconds):
        if isinstance(idle_seconds,bool) or not math.isfinite(idle_seconds) or idle_seconds <= 0:
            raise ValueError('연결 무응답 한도는 0 초과의 유한한 초 단위 값이어야 합니다.')
        self.idle_seconds = idle_seconds
        self.started = self.last_received = None
        self.connected_at = self.last_message_at = self.last_pingpong_at = self.last_trade_message_at = None
        self.messages = self.connection_messages = self.pingpongs = 0

    def connected(self, monotonic_now, wall_time):
        # Arm after sending subscriptions: queued initial acknowledgments are
        # not mistaken for a dead connection while registration is in progress.
        self.started, self.last_received = monotonic_now, None
        self.connected_at = wall_time
        self.connection_messages = 0

    def received(self, monotonic_now, wall_time, kind='message'):
        self.last_received = monotonic_now
        self.last_message_at = wall_time
        self.messages += 1
        self.connection_messages += 1
        if kind == 'pingpong':
            self.last_pingpong_at = wall_time
            self.pingpongs += 1
        elif kind == 'trade':
            self.last_trade_message_at = wall_time

    def silence_seconds(self, monotonic_now):
        reference = self.last_received if self.last_received is not None else self.started
        return None if reference is None else max(0.,monotonic_now-reference)

    def expired(self, monotonic_now):
        silence = self.silence_seconds(monotonic_now)
        return silence is not None and silence >= self.idle_seconds

    def snapshot(self, monotonic_now):
        return {'idle_timeout_seconds':self.idle_seconds,'connected_at':self.connected_at,
            'last_message_at':self.last_message_at,'last_pingpong_at':self.last_pingpong_at,
            'last_trade_message_at':self.last_trade_message_at,'messages_received':self.messages,
            'connection_messages_received':self.connection_messages,'pingpongs_received':self.pingpongs,
            'receive_silence_seconds':self.silence_seconds(monotonic_now)}
