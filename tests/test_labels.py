"""beeper labels sync — driven through sync() against a fake Beeper API."""
from beeper_triage import labels


class FakeBeeper:
    """Just enough of the Desktop API: chats, /v1/labels, space state,
    createRoom, leave. Rooms are {id: (name, children, look)}."""

    def __init__(self, chats, rooms):
        self.chats = chats
        self.rooms = rooms
        self.left = []
        self.n = 0

    def __call__(self, method, path, query=None, body=None):
        if path == "/v1/chats":
            return {"items": self.chats, "hasMore": False}
        if path == "/v1/labels":
            return [{"id": rid, "name": r[0]} for rid, r in self.rooms.items() if rid not in self.left]
        if path.endswith("/createRoom"):
            self.n += 1
            rid = f"!new{self.n}"
            kids = {e["state_key"] for e in body["initial_state"] if e["type"] == "m.space.child"}
            look = {e["type"]: e["content"] for e in body["initial_state"] if e["state_key"] == ""}
            self.rooms[rid] = (body["name"], kids, look)
            return {"room_id": rid}
        rid = path.split("/rooms/")[1].split("/")[0].replace("%21", "!").replace("%3A", ":")
        if path.endswith("/leave"):
            self.left.append(rid)
            return {}
        if path.endswith("/state"):
            name, kids, look = self.rooms[rid]
            return ([{"type": "m.space.child", "state_key": k, "content": {"via": ["x"]}} for k in kids]
                    + [{"type": t, "state_key": "", "content": c} for t, c in look.items()])
        raise AssertionError(path)

    def members(self, name):
        live = [r for rid, r in self.rooms.items() if r[0] == name and rid not in self.left]
        assert len(live) == 1, live
        return live[0][1]


def dm(cid, phone=None, network="WhatsApp", **kw):
    parts = [{"isSelf": True, "phoneNumber": "+447000000000"}, {"phoneNumber": phone}]
    return {"id": cid, "type": "single", "network": network,
            "participants": {"items": parts}, **kw}


def person(name, phone):
    return {"names": [{"displayName": name}], "phoneNumbers": [{"value": phone}]}


def test_sync_adds_keeps_hand_filed_and_removes_only_its_own(tmp_path):
    ledger = tmp_path / "ledger.json"
    chats = [
        dm("!bob", "+447700900001"),
        dm("!bob-sms", "+447700900001", network="Google Messages"),
        dm("!bob-ig", None, network="Instagram", mergedIntoChatID="!bob-merged"),
        {"id": "!bob-merged", "type": "single", "merge": {"chatIDs": ["!bob", "!bob-ig"]}},
        dm("!ann", "+447700900002"),
        dm("!mine", "+447700900003"),   # filed by hand, not in Google
        {"id": "!grp", "type": "group", "participants": {"items": [{"phoneNumber": "+447700900001"}]}},
    ]
    fake = FakeBeeper(chats, {"!old": ("Contractors", {"!mine"}, {"com.beeper.label.color": {"color_index": 3}})})

    groups = {"Contractors": [person("Bob", "07700 900001"), person("Ann", "07700 900002")]}
    labels.sync(fake, groups, apply=True, ledger_path=ledger)
    # Merged Instagram chat rides along with Bob's WhatsApp; the group never does.
    assert fake.members("Contractors") == {"!bob", "!bob-sms", "!bob-ig", "!bob-merged", "!ann", "!mine"}
    assert fake.left == ["!old"]
    assert fake.rooms["!new1"][2]["com.beeper.label.color"] == {"color_index": 3}  # colour carried

    # Ann leaves the Google label: her chat goes, the hand-filed one stays.
    groups = {"Contractors": [person("Bob", "07700 900001")]}
    labels.sync(fake, groups, apply=True, ledger_path=ledger)
    assert fake.members("Contractors") == {"!bob", "!bob-sms", "!bob-ig", "!bob-merged", "!mine"}

    # Nothing changed -> no rebuild.
    before = fake.n
    labels.sync(fake, groups, apply=True, ledger_path=ledger)
    assert fake.n == before


def test_first_run_on_an_unrecorded_label_removes_nothing(tmp_path):
    # No ledger yet: chats already in the label that Google doesn't know about
    # might be hand-filed, so they are adopted, not removed.
    fake = FakeBeeper([dm("!ann", "+447700900002"), dm("!x", "+447700900009")],
                      {"!old": ("Friends", {"!x"}, {})})
    plans = labels.sync(fake, {"Friends": [person("Ann", "07700900002")]},
                        apply=True, ledger_path=tmp_path / "l.json")
    assert plans[0].remove == set()
    assert fake.members("Friends") == {"!ann", "!x"}


def test_dry_run_writes_nothing(tmp_path):
    fake = FakeBeeper([dm("!ann", "+447700900002")], {})
    plans = labels.sync(fake, {"Friends": [person("Ann", "07700900002")]},
                        apply=False, ledger_path=tmp_path / "l.json")
    assert plans[0].add == {"!ann"} and fake.n == 0 and not (tmp_path / "l.json").exists()


def test_norm_phone():
    assert labels.norm_phone("07730 784352") == "+447730784352"
    assert labels.norm_phone("+44 7730 784352") == "+447730784352"
    assert labels.norm_phone("00254725717215") == "+254725717215"
    assert labels.norm_phone("") == ""
