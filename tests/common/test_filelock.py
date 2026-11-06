import threading
from src.common.filelock import file_lock


def test_lock_serializes(tmp_path):
    p = tmp_path / "x.json"
    counter = {"n": 0, "max": 0, "in": 0}

    def work():
        for _ in range(20):
            with file_lock(p):
                counter["in"] += 1
                counter["max"] = max(counter["max"], counter["in"])
                counter["n"] += 1
                counter["in"] -= 1

    ts = [threading.Thread(target=work) for _ in range(4)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert counter["n"] == 80 and counter["max"] == 1
