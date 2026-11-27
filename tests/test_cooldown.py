from sentry.cooldown import Cooldown


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def test_fires_once_per_period_and_per_key():
    clock = FakeClock()
    cd = Cooldown(10, clock)
    assert cd.ready(1)
    assert not cd.ready(1)
    assert cd.ready(2)  # another person is independent
    clock.now = 9.9
    assert not cd.ready(1)
    clock.now = 10.0
    assert cd.ready(1)
