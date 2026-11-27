"""fx/context.py: bindings of units, missiles, overlays and skills; the attach / missile / skill / scene drivers; the CLI.
Synthetic JSONs and tables in a temp folder, a fake skeleton and a stub Sim: no game files needed. A smoke test over real
files runs only when HDFX_TEST_ROOT (a folder with hd/...) is set."""
import io
import json
import math
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

try:
    import numpy, PIL, scipy  # noqa: E401,F401  (hdkit's own dependencies: pip install -r hdkit/requirements.txt)
except ImportError as _e:
    raise unittest.SkipTest("hdkit needs numpy, Pillow and scipy (hdkit/requirements.txt): %s" % _e)
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from hdkit.fx import context as C  # noqa: E402
from hdkit.view import vfs  # noqa: E402


def comp(t, **kw):
    return dict(type=t, name=t, **kw)


def xf(pos=(0, 0, 0), scale=(1, 1, 1), ori=(0, 0, 0, 1), only_pos=False):
    return comp("TransformDefinitionComponent", position=dict(zip("xyz", pos)), scale=dict(zip("xyz", scale)),
                orientation=dict(zip("xyzw", ori)), inheritOnlyPosition=only_pos)


def ent(name, *comps):
    return {"type": "Entity", "name": name, "id": 1, "components": list(comps)}


def vfx(path):
    return comp("VfxDefinitionComponent", filename=path, hardKillOnDestroy=False)


FILES = {
    "hd/missiles/missiles.json": {"dependencies": {}, "firebolt": "fire_bolt", "fire_bolt_1": "fire_bolt", "boom_1": "boom"},
    "hd/missiles/fire_bolt.json": {"type": "UnitDefinition", "name": "fire_bolt", "entities": [
        ent("root", comp("UnitRootComponent", rotationOverride=dict(x=0, y=0.38, z=0, w=0.92), state_machine_filename="")),
        ent("v1", xf((0, 4, 1), (2, 2, 2)), vfx("data/hd/vfx/particles/missiles/fb.particles"),
            comp("TrackReleasePointDefinitionComponent", transitionTime=0.5)),
        ent("v2", xf((0, 1, 0)), vfx("data/hd/vfx/particles/missiles/fb_vampire.particles"),
            comp("UnitSourceSkillFilterComponent", sourceSkill="VampireBolt")),
        ent("empty", xf(), vfx("")),
        ent("light", xf((0, 3, 0)), comp("SpotLightDefinitionComponent", color=dict(x=1, y=.5, z=0), power=5, distance=9))]},
    "hd/missiles/boom.json": {"type": "UnitDefinition", "name": "boom", "entities": [
        ent("v", xf(), vfx("data/hd/vfx/particles/missiles/boom.particles"))]},
    "hd/overlays/common/cast_a.json": {"type": "OverlayDefinition", "name": "cast_a", "entities": [
        ent("v", vfx("data/hd/vfx/particles/overlays/cast_a.particles"))]},
    "hd/overlays/sorceress/cast_b.json": {"type": "OverlayDefinition", "name": "cast_b", "entities": [
        ent("v", vfx("data/hd/vfx/particles/overlays/cast_b.particles"))]},
    "hd/character/enemy/ghoul1.json": {"type": "UnitDefinition", "name": "ghoul1", "entities": [
        ent("root", comp("UnitRootComponent", rotationOverride={}, state_machine_filename="x.json"),
            xf(scale=(1.5, 1.5, 1.5)), comp("SkeletonDefinitionComponent", filename="hd/character/enemy/ghoul1/g.skeleton"),
            comp("DamageFXComponent", vfxSpawnBones=["spine"], attackAttemptVfxOverrides={},
                 attackSuccessVfxOverrides={"name": "Succ", "rules": [{"vfxFile": "hit.particles", "acceptedHitClasses": 3,
                                                                       "acceptedVictims": 1, "acceptedBloodTypes": 7}],
                                            "defaultVfxFile": ""})),
        ent("fire", vfx("data/hd/vfx/particles/character/enemy/ghoul1/ghoul_fire.particles"),
            comp("EntityAttachmentDefinitionComponent", targetbone="hand"), xf((0, .2, 0), only_pos=True)),
        ent("glow", comp("ModelDefinitionComponent", filename="m.model"), comp("ObjectEffectDefinitionComponent", filename="g.particles",
                                                                             isWarlockWeaponVfx=False))]},
    "hd/character/monsters.json": {"ghoul2": "ghoul1", "ghoul4": "ghoul3"},
    # a unit with a name-bound overlay, a preload list, a state machine and animation timelines
    "hd/character/enemy/ghoul3.json": {"type": "UnitDefinition", "name": "ghoul3", "entities": [
        ent("root", comp("UnitRootComponent", rotationOverride={}, state_machine_filename="data/hd/character/enemy/ghoul3/sm.json"),
            comp("SkeletonDefinitionComponent", filename="data/hd/character/enemy/ghoul3/skeleton/g.skeleton"),
            comp("DefinitionPreloadComponent", overlays=[{"filename": "data/hd/overlays/monster/ghoul3.json"},
                                                         {"filename": "data/hd/overlays/common/cast_a.json"},
                                                         {"filename": "data/hd/overlays/common/missing.json"}], missiles=[])),
        ent("fire", vfx("data/hd/vfx/particles/g3/fire.particles"), comp("EntityAttachmentDefinitionComponent", targetbone="Hand"))]},
    "hd/overlays/monster/ghoul3.json": {"type": "OverlayDefinition", "name": "ghoul3", "entities": [
        ent("entity_root", vfx("data/hd/vfx/particles/g3/Smoke.particles"))]},
    "hd/character/enemy/ghoul3/sm.json": {"states": [{"name": "State_Foo", "modeId": 9}, {"name": "State_Death", "modeId": 0}]},
    "hd/character/enemy/ghoul3/animation/combined.timelines": {"type": "AnimationTimelineSet", "timelines": {
        "death": {"animationEvents": [
            {"type": "SpawnVFX", "name": "late", "eventTimestamp": 0.5, "duration": 0.0, "eventParams": [
                {"parameterName": "filename", "value": "data/hd/vfx/particles/g3/death.particles"},
                {"parameterName": "boneName", "value": "Hand"}, {"parameterName": "attachToEntity", "value": False},
                {"parameterName": "translationOffset", "value": {"x": 0, "y": 1, "z": 0}},
                {"parameterName": "rotationOffset", "value": {"x": 0, "y": 0, "z": 0}},
                {"parameterName": "attachToDeadBody", "value": False}]},
            {"type": "SpawnObjectEffect", "name": "obj", "eventTimestamp": 0.0, "duration": 1.5, "eventParams": [
                {"parameterName": "filename", "value": "data/hd/vfx/particles/g3/death_obj.particles"},
                {"parameterName": "unitPart", "value": "special1"}]},
            {"type": "PlaySound", "name": "snd", "eventTimestamp": 0.1, "eventParams": []}]},
        "foo": {"animationEvents": [
            {"type": "SpawnVFX", "name": "e", "eventTimestamp": 0.2, "duration": 0.0, "eventParams": [
                {"parameterName": "filename", "value": "data/hd/vfx/particles/g3/foo.particles"},
                {"parameterName": "boneName", "value": "Hand"}, {"parameterName": "attachToEntity", "value": True},
                {"parameterName": "rotationOffset", "value": {"x": 0, "y": 90, "z": 0}}]},
            {"type": "SpawnVFX", "name": "root", "eventTimestamp": 0.1, "duration": 0.0, "eventParams": [
                {"parameterName": "filename", "value": "data/hd/vfx/particles/g3/foo_root.particles"},
                {"parameterName": "boneName", "value": "root"}, {"parameterName": "attachToEntity", "value": False}]}]}}},
}
TABLES = {
    "missiles": "Missile\t*ID\tVel\tMaxVel\tAccel\tRange\tLight\tRed\tGreen\tBlue\tExplosionMissile\tCltSubMissile1\n"
                "firebolt\t0\t20\t30\t4\t40\t5\t255\t100\t0\tboom 1\t\n"
                "Expansion\n"
                "boom 1\t1\t0\t0\t0\t12\t3\t0\t0\t0\t\t\n",
    "overlay": "overlay\t*ID\tFilename\tCharacter\tHeight1\tHeight2\tHeight3\tHeight4\ncast a\t0\tcast_a\tall\ncast b\t1\tcast_b\tSorceress\n"
               "ghost\t2\tx\tall\nghoul3\t3\tx\tmonster\t0\t5\t10\t15\n",
    "monstats": "Id\tSkill1\tSk1mode\tSk1lvl\tSkill2\tSkill3\nghoul3\tFire Bolt\tA1\t3\tNoSuchSkill\t\nnounit\t\t\t\t\t\n",
    "monstats2": "Id\tOverlayHeight\nghoul3\t4\nghoul1\t0\n",
    "states": "state\t*ID\toverlay1\toverlay2\tcastoverlay\tremoverlay\nburn\t7\tcast a\t\tcast b\tcast a\nplain\t8\t\t\t\t\n",
    "skills": "skill\t*Id\tcltmissile\tsrvmissile\tcastoverlay\tcltoverlaya\tanim\tmonanim\n"
              "Fire Bolt\t36\tfirebolt\tfirebolt\tcast a\tcast b\tSC\txx\nKick\t99\t\t\t\t\tKK\t\nNothing\t5\t\t\t\t\t\t\n",
}


def write_tree(root: Path):
    for k, v in FILES.items():
        p = root / k
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(v), encoding="utf-8")
    ex = root / "excel"
    ex.mkdir()
    for k, v in TABLES.items():
        (ex / (k + ".txt")).write_text(v, encoding="utf-8")


class FakeSkeleton:
    names = ["root", "Hand"]
    idx = {"root": 0, "Hand": 1}
    parent = [-1, 0]

    def worlds(self, loc):
        return np.stack(loc)

    def rest_locals(self):
        return [np.eye(4), C._translate((0.0, 1.0, 0.0))]


class FakePoses:
    """root fixed, Hand moves +1 x per second at height 1."""
    duration = 2.0
    sk = FakeSkeleton()

    def worlds(self, t):
        return np.stack([np.eye(4), C._translate((t, 1.0, 0.0))])

    def index(self, bone):
        return C._bone_index(self.sk, bone)


class FakeUnit:
    class P:
        skeleton = FakeSkeleton()
    parts = [P()]
    root_scale = 1.0


class StubSim:
    """The minimal Sim protocol of docs/hdfx.md section 2: Sim(effect, seed, ctx), step(dt), state()."""
    made = []

    def __init__(self, effect, ctx, seed):
        self.effect, self.ctx, self.seed, self.t, self.log = effect, ctx, seed, 0.0, []
        StubSim.made.append(self)

    def step(self, dt):
        self.t += dt
        self.log.append(self.ctx.transform[:3, 3].copy())

    def state(self):
        return {"t": self.t, "pos": self.ctx.transform[:3, 3].copy()}


class Fixture(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._td = tempfile.TemporaryDirectory()
        cls.root = Path(cls._td.name)
        write_tree(cls.root)
        cls.fs = vfs.Layered([("t", cls.root)])
        cls.tb = C.Tables.from_dir(cls.root / "excel")

    @classmethod
    def tearDownClass(cls):
        cls._td.cleanup()


class TestBindings(Fixture):
    def test_norm_key(self):
        self.assertEqual(C.norm_key("Spike1"), "spike_1")
        self.assertEqual(C.norm_key("cyclonearmor1front"), "cyclonearmor_1front")
        self.assertEqual(C.norm_key("Boom 1"), "boom_1")

    def test_tables(self):
        self.assertEqual(self.tb.row("missiles", "BOOM 1")["range"], "12")
        self.assertIsNone(self.tb.row("missiles", "expansion"))
        self.assertEqual(self.tb.row("skills", "#36")["skill"], "Fire Bolt")
        self.assertFalse(self.tb.has("monai"))

    def test_missile(self):
        m = C.effects_of_missile(self.fs, "firebolt", self.tb)
        self.assertEqual((m.model, m.path), ("fire_bolt", "hd/missiles/fire_bolt.json"))
        self.assertEqual([b.entity for b in m.vfx()], ["v1"])                   # skill-filtered and empty entities left out
        self.assertEqual([b.entity for b in m.vfx("vampirebolt")], ["v1", "v2"])
        b = m.vfx()[0]
        self.assertEqual(b.position, (0.0, 4.0, 1.0))
        self.assertTrue(b.release_point)
        np.testing.assert_allclose(b.xf[:3, 3], (0, 4, 1))
        np.testing.assert_allclose(np.diag(b.xf)[:3], (2, 2, 2))
        self.assertEqual(m.bindings[-1].kind, "light")
        self.assertAlmostEqual(m.row["speed_units_s"], 20 * 2.34375)
        self.assertAlmostEqual(m.row["max_speed_units_s"], 30 * 2.34375 / 0.75)   # MaxVel has no 75 % cut (calib4)
        self.assertEqual(m.children, [("explosionmissile", "boom 1")])
        self.assertAlmostEqual(m.rotation_override[1], 0.38)

    def test_missile_unknown(self):
        m = C.effects_of_missile(self.fs, "nothing_here")
        self.assertIsNone(m.path)
        self.assertTrue(m.notes)
        self.assertEqual(m.row, {})

    def test_overlay_character_folder(self):
        self.assertEqual(C.effects_of_overlay(self.fs, "cast a", self.tb).folder, "common")     # `all` is D2R's `common`
        o = C.effects_of_overlay(self.fs, "cast b", self.tb)
        self.assertEqual((o.folder, o.path), ("sorceress", "hd/overlays/sorceress/cast_b.json"))
        wrong = C.effects_of_overlay(self.fs, "cast_b")                  # no table: found by walking the folders
        self.assertEqual(wrong.folder, "sorceress")
        self.assertTrue(wrong.notes)
        self.assertIsNone(C.effects_of_overlay(self.fs, "ghost", self.tb).path)

    def test_skill(self):
        s = C.effects_of_skill(self.fs, "fire bolt", self.tb)
        cols = [(r.column, r.kind) for r in s.refs]
        self.assertEqual(cols, [("cltmissile,srvmissile", "missile"), ("castoverlay", "overlay"), ("cltoverlaya", "overlay")])
        self.assertEqual(s.mode, "sc")
        self.assertEqual(C.effects_of_skill(self.fs, "Kick", self.tb).mode, "a2")
        self.assertTrue(C.effects_of_skill(self.fs, "Nothing", self.tb).notes)
        with self.assertRaises(KeyError):
            C.effects_of_skill(self.fs, "nope", self.tb)

    def test_unit(self):
        u = C.effects_of_unit(self.fs, "ghoul1")
        self.assertAlmostEqual(u.root_scale, 1.5)
        f = [b for b in u.vfx() if b.kind == "vfx"][0]
        self.assertEqual((f.bone, f.inherit_only_position), ("hand", True))
        self.assertEqual(u.skeleton, "hd/character/enemy/ghoul1/g.skeleton")
        self.assertEqual([b.kind for b in u.vfx()], ["vfx", "objecteffect"])
        self.assertEqual([h["file"] for h in u.hit if "file" in h], ["hit.particles"])
        self.assertIn({"table": "spawn_bone", "bone": "spine"}, u.hit)
        self.assertEqual(C.effects_of_unit(self.fs, "ghoul2").unit, "ghoul1")       # monsters.json alias
        with self.assertRaises(FileNotFoundError):
            C.effects_of_unit(self.fs, "zzz")


class TestAttach(unittest.TestCase):
    def test_bone_follow_and_velocity(self):
        d = C.attach("fx.particles", FakeUnit(), "hand", poses=FakePoses())
        f = d.frame(1.0)
        np.testing.assert_allclose(f.ctx.transform[:3, 3], (1.0, 1.0, 0.0))
        np.testing.assert_allclose(f.ctx.velocity, (1.0, 0.0, 0.0), atol=1e-6)
        np.testing.assert_allclose(f.ctx.bones("hand")[:3, 3], (1.0, 1.0, 0.0))      # case-insensitive bone name
        np.testing.assert_allclose(f.ctx.bones(None)[:3, 3], (0, 0, 0))
        self.assertEqual(f.ctx.ground_z, 0.0)
        self.assertEqual(len(list(d.frames(25.0))), 51)
        with self.assertRaises(KeyError):
            d.frame(0.0).ctx.bones("tail")

    def test_place_yaw_scale_and_local(self):
        class U(FakeUnit):
            root_scale = 2.0
        b = C.Binding("vfx", "e", "x.particles", "Hand", C._translate((0, 0, 1)))
        d = C.attach(b, U(), None, poses=FakePoses(), place=C._translate((10, 0, 5)), yaw=90.0)
        # unit frame: yaw 90 sends +z to +x, scale 2; hand at (1, 1, 0) in model space; local +z 1 (in the bone's frame)
        M = d.frame(1.0).ctx.transform
        np.testing.assert_allclose(M[:3, 3], (10 + 2 * 0 + 2 * 1, 2.0, 5 + 2 * -1.0), atol=1e-9)

    def test_inherit_only_position(self):
        b = C.Binding("vfx", "e", "x.particles", "Hand", np.eye(4), inherit_only_position=True)
        d = C.attach(b, FakeUnit(), None, poses=FakePoses(), yaw=90.0)
        np.testing.assert_allclose(d.frame(0.5).ctx.transform[:3, :3], np.eye(3), atol=1e-9)

    def test_rest_pose_without_poses(self):
        d = C.attach("x", FakeUnit(), "Hand")
        np.testing.assert_allclose(d.frame(0.3).ctx.transform[:3, 3], (0, 1, 0))
        self.assertEqual(d.frame(0.3).ctx.velocity.tolist(), [0, 0, 0])

    def test_attach_unit(self):
        class Fx:
            def vfx(self, skill=None):
                return [C.Binding("vfx", "a", "a.particles", "root"), C.Binding("vfx", "b", "b.particles", "Hand")]
        ds = C.attach_unit(Fx(), FakeUnit(), poses=FakePoses())
        self.assertEqual([d.layers[0].effect for d in ds], ["a.particles", "b.particles"])
        self.assertIs(ds[0].poses, ds[1].poses)


class TimelinePlayer:
    """Stub of view.player.Player for PlayerPoses: a timeline of frames and a pose per frame (hand at x = frame time)."""
    def timeline(self, steps, fps=25.0):
        return [type("F", (), {"time": i / fps})() for i in range(int(round(sum(s for _m, s in steps) * fps)))]

    def pose(self, sk, frame=None):
        return type("P", (), {"worlds": FakePoses().worlds(frame.time)})()


class TestPlayerPoses(unittest.TestCase):
    def test_timeline_poses_and_attach(self):
        pp = C.PlayerPoses(TimelinePlayer(), FakeSkeleton(), [("a1", 1.0), ("nu", 1.0)], fps=10.0)
        self.assertAlmostEqual(pp.duration, 2.0)
        np.testing.assert_allclose(pp.worlds(0.5)[1][:3, 3], (0.5, 1.0, 0.0))
        np.testing.assert_allclose(pp.worlds(99.0)[1][:3, 3], (1.9, 1.0, 0.0))        # clamped to the last frame
        d = C.attach("x", FakeUnit(), "Hand", player=TimelinePlayer(), steps=[("a1", 2.0)])
        self.assertAlmostEqual(d.duration, 2.0)
        np.testing.assert_allclose(d.frame(1.0).ctx.transform[:3, 3], (1.0, 1.0, 0.0), atol=1e-9)


class TestMissile(unittest.TestCase):
    def test_constant_speed_events_and_spawn(self):
        m = C.missile("bolt.particles", start=(0, 0, 0), end=(0, 0, 30), speed=10.0, events={"hit": "h.particles", "explode": "e.particles"})
        self.assertAlmostEqual(m.t_end, 3.0, places=5)
        np.testing.assert_allclose(m.frame(1.5).ctx.transform[:3, 3], (0, 0, 15), atol=1e-6)
        np.testing.assert_allclose(m.frame(1.5).ctx.velocity, (0, 0, 10), atol=1e-2)
        names = [e[1] for e in m.events]
        self.assertEqual(names[0], "launch")
        self.assertEqual(m.event_times()["hit"], m.t_end)
        self.assertEqual([c.layers[0].effect for _t, c in m.children], ["h.particles", "e.particles"])
        self.assertAlmostEqual(m.children[0][0], 3.0, places=5)
        np.testing.assert_allclose(m.children[0][1].M[:3, 3], (0, 0, 30), atol=1e-6)
        np.testing.assert_allclose(m.frame(5.0).ctx.velocity, 0)             # after the hit: standing
        ev = m.frame(3.1, prev=2.9).events
        self.assertEqual({e[1] for e in ev}, {"hit", "explode", "spawn"})
        self.assertEqual(m.frame(1.0, prev=0.5).events, [])

    def test_faces_flight_direction(self):
        m = C.missile("x", start=(0, 0, 0), end=(10, 0, 0), speed=5.0)       # towards +x: yaw 90
        R = m.frame(0.5).ctx.transform[:3, :3]
        np.testing.assert_allclose(R @ [0, 0, 1], (1, 0, 0), atol=1e-9)

    def test_accel_and_max_speed(self):
        m = C.MissileDriver([C.Layer("x")], [(0, 0, 0), (0, 0, 100)], 0.0, accel=2.0)
        self.assertAlmostEqual(m.distance_at(3.0), 9.0)
        m2 = C.MissileDriver([C.Layer("x")], [(0, 0, 0), (0, 0, 100)], 1.0, accel=1.0, max_vel=3.0)
        self.assertAlmostEqual(m2.distance_at(2.0), 1 * 2 + 2.0)           # v 1 -> 3 over 2 s
        self.assertAlmostEqual(m2.distance_at(4.0), 4.0 + 3.0 * 2)         # then constant
        self.assertAlmostEqual(m2.distance_at(m2.time_of_distance(37.5)), 37.5, places=6)

    def test_row_units_and_range_expiry(self):
        tb = C.Tables({"missiles": TABLES["missiles"]})
        row = C.missile_facts(tb.row("missiles", "firebolt"))
        m = C.missile("x", start=(0, 0, 0), end=(0, 0, 500), row=row)
        self.assertAlmostEqual(m.v0, 20 * C.VEL_UNITS_PER_S)
        self.assertFalse(m.reached)                                          # Range 40 frames = 1.6 s: expires first
        self.assertAlmostEqual(m.t_end, 1.6, places=3)
        self.assertEqual(m.event_times().keys(), {"launch", "expire", "explode"})
        self.assertGreater(m.distance_at(m.t_end), 40.0)                      # accelerating from 31 u/s

    def test_arc_and_path(self):
        m = C.missile("x", path=[(0, 0, 0), (0, 0, 10), (10, 0, 10)], speed=10.0, arc=3.0)
        self.assertAlmostEqual(m.length, 20.0)
        self.assertAlmostEqual(m.position(1.0)[1], 3.0 * 4 * 0.5 * 0.5)
        self.assertEqual(m.position(2.0).tolist(), [10.0, 0.0, 10.0])

    def test_errors(self):
        with self.assertRaises(ValueError):
            C.missile("x", start=(0, 0, 0), end=(1, 0, 0))
        with self.assertRaises(ValueError):
            C.missile("x", speed=1.0)

    def test_layers_of_missile_effects(self):
        b = C.Binding("vfx", "e", "a.particles", xf=C._translate((0, 4, 1)))
        m = C.missile([b, "other"], start=(0, 0, 0), end=(0, 0, 10), speed=10)
        np.testing.assert_allclose(m.frame(0.0).contexts[0].transform[:3, 3], (0, 4, 1))
        np.testing.assert_allclose(m.frame(0.0).contexts[1].transform[:3, 3], (0, 0, 0))


class StubPlayer:
    timing = "clip"
    classic = {"sc": {"frames": 12, "speed": 256}}

    def length(self, mode):
        return 1.2 if mode in ("sc", 7) else 0.5


class TestSkillAndDrive(unittest.TestCase):
    def test_cast_time(self):
        self.assertAlmostEqual(C.cast_time(StubPlayer(), "sc", 6), 0.6)            # frame 6 of 12 over 1.2 s
        self.assertAlmostEqual(C.cast_time(None, "sc", 5), 0.2)                    # classic 25 fps
        self.assertAlmostEqual(C.cast_time(StubPlayer(), "a1", 5), 0.2)           # no classic data for the mode

    def test_skill_children(self):
        ov, mi = C.Layer("ov.particles"), C.Layer("mi.particles")
        d = C.skill([("overlay", ov), ("missile", mi)], FakeUnit(), 10, "sc", poses=FakePoses(), target=(0, 0, 40),
                    bone="Hand", skill_name="x")
        self.assertAlmostEqual(d.trigger_t, 0.4)
        self.assertEqual(d.events[0][:2], (0.4, "cast"))
        starts = [(t, c.kind) for t, c in d.children]
        self.assertEqual(starts, [(0.4, "attach"), (0.4, "missile")])
        miss = d.children[1][1]
        np.testing.assert_allclose(miss.path[0], (0.4, 1.0, 0.0))                  # leaves the hand where it is at the trigger
        self.assertEqual(miss.start, 0.4)
        np.testing.assert_allclose(miss.frame(0.4).ctx.transform[:3, 3], (0.4, 1.0, 0.0), atol=1e-6)
        ovd = d.children[0][1]
        np.testing.assert_allclose(ovd.frame(0.4).ctx.transform[:3, 3], (0, 0, 0))   # an overlay sits on the unit's root

    def test_drive_plays_children(self):
        StubSim.made = []
        m = C.missile("bolt", start=(0, 0, 0), end=(0, 0, 5), speed=5.0, events={"explode": "boom"}, tail=0.2)
        out = list(C.drive(m, StubSim, fps=10))
        self.assertEqual([s.effect for s in StubSim.made], ["bolt", "boom"])
        self.assertAlmostEqual(out[0][0], 0.0)
        self.assertEqual(len(out[0][1]), 1)                                        # the explosion has not started
        self.assertEqual(len(out[11][1]), 2)                                       # t = 1.1 s: both play
        self.assertEqual(len(out[-1][1]), 1)                                       # t = 4 s: only the explosion's tail
        self.assertAlmostEqual(StubSim.made[0].log[5][2], 2.5, places=3)           # t = 0.5 s at 5 u/s
        self.assertEqual(StubSim.made[1].log[0][2], 5.0)


class TestScene(Fixture):
    VIS = {"entities": [
        {"name": "vis", "components": [comp("VisualDataComponent", exposureValue=4.0, characterBrightnessFactor=0.4, bloomCutoff=0.9,
                                           bloomIntensity=1.5, iblIntensity=2.0, gradingLutFile="data/hd/env/lut/default.dds")]},
        {"name": "global_key_light", "components": [comp("DirectionalLightDefinitionComponent", power=3, color=dict(x=1, y=1, z=1),
                                                         lightMask=1), xf(ori=(0.3, 0.2, 0, 0.93))]}]}

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        files = {
            "hd/env/preset/act9/town.json": {"biomeFilename": "act9", "entities": [
                {"name": "l1", "components": [comp("PointLightDefinitionComponent", color=dict(x=1, y=1, z=1), power=400, radius=20),
                                              xf((52, 3, 51))]},
                {"name": "far", "components": [comp("PointLightDefinitionComponent", color=dict(x=1, y=1, z=1), power=400, radius=5),
                                               xf((900, 3, 900))]}]},
            "hd/env/biome/act9.json": {"visualDataFilenames": [{"visualDataFilename": "hd/env/vis/night.json"},
                                                              {"visualDataFilename": "hd/env/vis/day.json"}]},
            "hd/env/vis/day.json": cls.VIS,
        }
        for k, v in files.items():
            p = cls.root / k
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps(v))

    def test_light_preset_values(self):
        lp = C.light_preset(self.VIS)
        self.assertAlmostEqual(lp.post.exposure, 0.18 / (2 ** 4 * 10 / 65))
        self.assertEqual(lp.post.tone, "lut")
        self.assertEqual(lp.post.bloom, {"cutoff": 0.9, "intensity": 1.5, "levels": 5, "gain": 1.0})
        self.assertEqual(lp.post.lut, "data/hd/env/lut/default.dds")
        self.assertEqual(set(lp.post.as_kwargs()), {"bloom", "tonemap", "exposure"})
        self.assertTrue(lp.unknown)                                              # what the viewer does not know is listed
        g = C.light_preset(None, tone="game")
        self.assertIsNone(g.post.bloom)
        self.assertEqual(g.post.tone, "game")

    def test_scene_spot(self):
        sc = C.scene("town", (5.2, 5.1), radius=3, fs=self.fs, preset_path="hd/env/preset/act9/town.json")
        np.testing.assert_allclose(sc.world, (52.0, 0.0, 51.0))
        self.assertEqual(sc.vis, "hd/env/vis/day.json")                        # `day` preferred over the first listed
        self.assertEqual([l["name"] for l in sc.lights], ["l1"])               # the far light is out of reach
        self.assertEqual(sc.light.post.bloom["cutoff"], 0.9)
        self.assertAlmostEqual(sc.light.light.ambient[0], 0.004 * 2.0)
        self.assertEqual(len(sc.light.light.points), 1 + 0)                    # preset light only (the vis key is directional)
        self.assertEqual((sc.frame.x0, sc.frame.x1), (2, 9))
        d = sc.place("boom.particles", offset=(1, 0, 0), yaw=90)
        np.testing.assert_allclose(d.frame(0).ctx.transform[:3, 3], (53, 0, 51))
        kw = sc.render_kwargs()
        self.assertEqual(kw["post"]["tonemap"], "lut")
        sub = C.scene("town", (26, 25.5), subtile=True, fs=self.fs, preset_path="hd/env/preset/act9/town.json")
        self.assertEqual(sub.xy, (5.2, 5.1))

    def test_scene_without_preset(self):
        sc = C.scene("nowhere", (1, 1), fs=self.fs)
        self.assertIsNone(sc.preset)
        self.assertTrue(sc.notes)
        self.assertEqual(sc.light.post.tone, "lut")


class TestCli(Fixture):
    def run_cli(self, *args):
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = C.main(list(args) + ["--root", "t=%s" % self.root, "--tables", str(self.root / "excel")])
        return rc, buf.getvalue()

    def test_missile_listing(self):
        rc, out = self.run_cli("missile", "firebolt")
        self.assertEqual(rc, 0)
        self.assertIn("fb.particles", out)
        self.assertIn("(explosionmissile) missile boom 1", out)
        self.assertIn("boom.particles", out)
        self.assertIn("46.88", out)

    def test_skill_unit_overlay_json(self):
        rc, out = self.run_cli("skill", "Fire Bolt")
        self.assertIn("cast_b.particles", out)
        self.assertIn("mode sc", out)
        rc, out = self.run_cli("unit", "ghoul1")
        self.assertIn("bone hand (position only)", out)
        rc, out = self.run_cli("overlay", "cast b", "--json")
        self.assertEqual(json.loads(out)["folder"], "sorceress")

class TestUnitBindings(Fixture):
    """Name-bound overlay, preload list, animation timelines, modes, overlay height, states and monsters."""

    def test_name_overlay_and_preload(self):
        u = C.effects_of_unit(self.fs, "ghoul3")
        by = {t: [b for b in u.bindings if b.trigger == t] for t in ("entity", "name", "preload", "timeline")}
        self.assertEqual([b.file for b in by["name"]], ["data/hd/vfx/particles/g3/Smoke.particles"])
        self.assertIsNone(by["name"][0].bone)
        np.testing.assert_allclose(by["name"][0].xf, np.eye(4))
        self.assertEqual(by["name"][0].extra["overlay"], "ghoul3")
        self.assertEqual([b.extra["overlay"] for b in by["preload"]], ["cast_a"])       # ghoul3 is played, missing.json is skipped
        self.assertEqual([b.file for b in u.vfx()], ["data/hd/vfx/particles/g3/fire.particles", "data/hd/vfx/particles/g3/Smoke.particles"])
        self.assertEqual(len(u.vfx(preload=True)), 3)                                     # preload shown on request
        self.assertEqual(C.effects_of_unit(self.fs, "ghoul1").unit, "ghoul1")
        self.assertEqual([b.trigger for b in C.effects_of_unit(self.fs, "ghoul1").bindings if b.kind == "vfx"], ["entity"])

    def test_name_rule_follows_the_unit_json(self):
        """`ghoul4` is a monsters.json key of unit ghoul3: the overlay named after the unit still plays (ubermephisto -> mephisto)."""
        u = C.effects_of_unit(self.fs, "ghoul4")
        self.assertEqual(u.unit, "ghoul3")
        self.assertEqual([b.trigger for b in u.bindings if b.file.endswith("Smoke.particles")], ["name"])

    def test_timeline_bindings(self):
        u = C.effects_of_unit(self.fs, "ghoul3")
        tl = u.timeline()
        self.assertEqual([(b.mode, b.time) for b in tl], [("dt", 0.0), ("dt", 0.5), ("s2", 0.1), ("s2", 0.2)])   # PlaySound dropped
        late = next(b for b in tl if b.entity.endswith("late"))
        self.assertEqual((late.bone, late.attach, late.trigger, late.kind), ("Hand", False, "timeline", "vfx"))
        np.testing.assert_allclose(late.xf[:3, 3], (0, 1, 0))
        obj = next(b for b in tl if b.kind == "objecteffect")
        self.assertEqual((obj.bone, obj.attach, obj.extra["unit_part"], obj.extra["duration"]), ("special1", None, True, 1.5))
        foo = next(b for b in tl if b.entity.endswith(":e"))
        self.assertEqual((foo.mode, foo.attach), ("s2", True))                            # state machine: State_Foo modeId 9 -> s2
        np.testing.assert_allclose(foo.xf[:3, :3] @ [0, 0, 1], (1, 0, 0), atol=1e-9)      # 90 degrees about y turns +z to +x
        self.assertEqual(foo.as_dict()["mode"], "s2")
        self.assertEqual(C.effects_of_unit(self.fs, "ghoul3", mode_map={"foo": "kb"}).timeline("kb")[0].mode, "kb")

    def test_applies_mode_skill_preload(self):
        u = C.effects_of_unit(self.fs, "ghoul3")
        files = lambda **kw: sorted(b.file.rsplit("/", 1)[-1] for b in u.vfx(**kw))
        self.assertEqual(files(), ["Smoke.particles", "fire.particles"])                  # timeline effects need their mode
        self.assertEqual(files(mode="dt"), ["Smoke.particles", "death.particles", "death_obj.particles", "fire.particles"])
        self.assertEqual(files(mode="DT"), files(mode="dt"))
        self.assertEqual(files(mode="a1"), files())
        b = C.Binding("vfx", "e", source_skill="Nova", trigger="timeline", mode="a1")
        self.assertFalse(b.applies("Nova"))
        self.assertFalse(b.applies("Nova", "a2"))
        self.assertFalse(b.applies("Other", "a1"))
        self.assertTrue(b.applies("nova", "a1"))
        self.assertEqual(C.timeline_bindings(self.fs, "hd/character/enemy/ghoul1.json"), [])      # no timelines file

    def test_overlay_height(self):
        self.assertEqual(C.effects_of_unit(self.fs, "ghoul3", self.tb).overlay_height, {"index": 4, "verified": False, "px": 15.0})
        self.assertIsNone(C.effects_of_unit(self.fs, "ghoul3").overlay_height)
        self.assertEqual(C.effects_of_overlay(self.fs, "ghoul3", self.tb, monster="ghoul3").height["px"], 15.0)
        self.assertEqual(C.effects_of_overlay(self.fs, "ghoul3", self.tb, height_index=2).height["px"], 5.0)
        self.assertIsNone(C.effects_of_overlay(self.fs, "cast a", self.tb).height)
        self.assertIsNone(C.overlay_height(self.tb, "ghoul1", None))                      # OverlayHeight 0

    def test_state_and_monster(self):
        st = C.effects_of_state(self.fs, "burn", self.tb)
        self.assertEqual([(r.column, r.name) for r in st.refs], [("overlay1", "cast a"), ("castoverlay", "cast b"), ("removerlay", "cast a")])
        self.assertEqual([b.trigger for b in st.bindings], ["state"] * 3)
        self.assertEqual([b.extra["column"] for b in st.vfx()], ["overlay1", "castoverlay", "removerlay"])
        self.assertEqual(C.effects_of_state(self.fs, "plain", self.tb).notes, ["no overlay column set"])
        self.assertEqual(st.as_dict()["refs"][0]["when"], "while active")
        with self.assertRaises(KeyError):
            C.effects_of_state(self.fs, "zzz", self.tb)
        m = C.effects_of_monster(self.fs, "ghoul3", self.tb)
        self.assertEqual(m.unit.unit, "ghoul3")
        self.assertEqual([(s["slot"], s["skill"], s["mode"]) for s in m.skills], [(1, "Fire Bolt", "A1"), (2, "NoSuchSkill", "")])
        self.assertEqual(m.skills[1]["note"], "not in skills.txt")
        self.assertEqual(m.skills[0]["effects"].refs[0].name, "firebolt")
        self.assertIn("no unit JSON", C.effects_of_monster(self.fs, "nounit", self.tb).notes[0])
        json.dumps(m.as_dict())

    def test_resolve_particle_case_insensitive(self):
        class Exact:                                        # a case-sensitive fs with walk()
            def __init__(self, files): self.files = files
            def exists(self, p): return p in self.files
            def walk(self, d, suffix): return [type("H", (), {"logical": f})() for f in self.files if f.startswith(d) and f.endswith(suffix.lower())]
        fs = Exact({"hd/vfx/particles/a/ambientfire.particles"})
        self.assertEqual(C.resolve_particle(fs, "data/hd/vfx/particles/a/ambientFire.particles"), "hd/vfx/particles/a/ambientfire.particles")
        self.assertIsNone(C.resolve_particle(fs, "data/hd/vfx/particles/a/other.particles"))
        self.assertEqual(C.resolve_particle(self.fs, "g3/x.particles"), None)
        self.fs.exists("hd/overlays/monster/ghoul3.json")
        self.assertEqual(C.resolve_particle(self.fs, "DATA/HD/Overlays/Monster/Ghoul3.json"), "hd/overlays/monster/ghoul3.json")


class TestTimelineDrivers(Fixture):
    def test_timeline_driver_death(self):
        u = C.effects_of_unit(self.fs, "ghoul3")
        d = C.timeline_driver(u, FakeUnit(), "dt", poses=FakePoses())
        self.assertEqual([(round(t, 3), c.kind) for t, c in d.children], [(0.0, "attach"), (0.5, "static")])
        self.assertEqual([e[:2] for e in d.events], [(0.0, "timeline"), (0.5, "timeline")])
        obj, late = d.children[0][1], d.children[1][1]
        self.assertEqual(obj.layers[0].bone, None)                                       # unitPart special1 is no bone: unit frame
        self.assertAlmostEqual(obj.duration, 1.5)
        # SpawnVFX with attachToEntity false stays where Hand was at 0.5 s (x = t), 1 up from the offset
        np.testing.assert_allclose(late.frame(0.5).ctx.transform[:3, 3], (0.5, 2.0, 0.0), atol=1e-9)
        np.testing.assert_allclose(late.frame(1.5).ctx.transform[:3, 3], (0.5, 2.0, 0.0), atol=1e-9)
        self.assertEqual(d.skipped, [])

    def test_attached_event_follows_the_bone_in_clip_time(self):
        u = C.effects_of_unit(self.fs, "ghoul3")
        d = C.timeline_driver(u, FakeUnit(), "s2", poses=FakePoses())
        kinds = [(round(t, 3), c.kind) for t, c in d.children]
        self.assertEqual(kinds, [(0.1, "static"), (0.2, "attach")])                       # `root` is the unit frame, not a bone
        np.testing.assert_allclose(d.children[0][1].frame(0.1).ctx.transform[:3, 3], (0, 0, 0), atol=1e-9)
        at = d.children[1][1]
        for t in (0.2, 0.8, 1.4):                                                         # pose time = driver time, not time since the start
            np.testing.assert_allclose(at.frame(t).ctx.transform[:3, 3], (t, 1.0, 0.0), atol=1e-9)

    def test_missing_bone_is_skipped_and_listed(self):
        u = C.effects_of_unit(self.fs, "ghoul3")
        u.bindings[-1].bone = "NoSuchBone"
        d = C.timeline_driver(u, FakeUnit(), u.bindings[-1].mode, poses=FakePoses())
        self.assertEqual(len(d.skipped), 1)
        self.assertIn("NoSuchBone", d.skipped[0][1])

    def test_event_time_scales_to_the_players_mode(self):
        pl = type("Pl", (), {"wclass": "hth", "sm": type("S", (), {"clip_name": lambda s, m, w: "c"})(),
                             "clips": {"c": type("K", (), {"duration": 2.0})()}, "length": lambda s, m: 1.0})()
        self.assertAlmostEqual(C.event_time(pl, "dt", 1.0), 0.5)                         # clip 2 s played over 1 s
        self.assertEqual(C.event_time(None, "dt", 1.0), 1.0)
        self.assertEqual(C.event_time(StubPlayer(), "dt", 1.0), 1.0)                     # a partial player: unchanged

    def test_skill_adds_unit_timeline_children(self):
        u = C.effects_of_unit(self.fs, "ghoul3")
        d = C.skill([("overlay", C.Layer("ov.particles"))], FakeUnit(), 10, "s2", poses=FakePoses(), unit_fx=u)
        self.assertEqual([(round(t, 3), c.kind) for t, c in d.children], [(0.1, "static"), (0.2, "attach"), (0.4, "attach")])
        self.assertIn("timeline", [e[1] for e in d.events])
        self.assertEqual([e[0] for e in d.events], sorted(e[0] for e in d.events))
        d2 = C.skill([], FakeUnit(), 10, "a1", poses=FakePoses(), unit_fx=u)
        self.assertEqual(d2.children, [])

    def test_static_poses_use_the_bind_pose(self):
        class Sk(FakeSkeleton):
            iwt = np.stack([np.linalg.inv(C._translate((0, 3, 0))), np.linalg.inv(C._translate((0, 5, 0)))])
        np.testing.assert_allclose(C.StaticPoses(Sk()).worlds(0)[1][:3, 3], (0, 5, 0), atol=1e-9)
        np.testing.assert_allclose(C.StaticPoses(FakeSkeleton()).worlds(0)[1][:3, 3], (0, 1, 0), atol=1e-9)    # no iwt: rest locals


class TestCliBindings(Fixture):
    def run_cli(self, *args):
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = C.main(list(args) + ["--root", "t=%s" % self.root, "--tables", str(self.root / "excel")])
        return rc, buf.getvalue()

    def test_unit_listing(self):
        rc, out = self.run_cli("unit", "ghoul3")
        self.assertIn("<name>", out)
        self.assertIn("<preload>", out)
        self.assertIn("<timeline dt @0.500s free>", out)
        self.assertIn("<timeline s2 @0.200s attached>", out)
        self.assertIn("overlay height", out)
        rc, out = self.run_cli("unit", "ghoul3", "--json")
        d = json.loads(out)
        self.assertEqual(sorted({b["trigger"] for b in d["bindings"] if b["kind"] != "model"}), ["entity", "name", "preload", "timeline"])
        self.assertEqual(d["overlay_height"]["px"], 15.0)

    def test_state_and_monster(self):
        rc, out = self.run_cli("state", "burn")
        self.assertIn("castoverlay (on cast / apply) = cast b", out)
        rc, out = self.run_cli("monster", "ghoul3")
        self.assertIn("skill1 Fire Bolt  mode A1", out)
        self.assertIn("(not in skills.txt)", out)
        self.assertEqual(json.loads(self.run_cli("monster", "ghoul3", "--json")[1])["id"], "ghoul3")


def _table(name, rules=(), default=""):
    return {"type": "AttackVFXTable", "name": name, "rules": [{"type": "AttackVFXRule", "name": "%s_%d" % (name, i), "vfxFile": f,
                                                               "acceptedHitClasses": 3, "acceptedVictims": 15, "acceptedBloodTypes": 7}
                                                              for i, f in enumerate(rules)], "defaultVfxFile": default}


MORE = {
    # ObjectEffect on a unit and on an item, success hit effects, a SpawnVFXConnected beam
    "hd/character/enemy/obj1.json": {"type": "UnitDefinition", "name": "obj1", "dependencies": {"objecteffects": [{"path": "data/p/never.particles"}]},
                                    "entities": [ent("entity_torso", comp("ModelDefinitionComponent", filename="t.model"),
                                                     comp("ObjectEffectDefinitionComponent", filename="data/p/obj1_objfx.particles")),
                                                 ent("empty", comp("ObjectEffectDefinitionComponent", filename="")),
                                                 ent("entity_root", comp("DamageFXComponent", attackSuccessVfxOverrides=_table(
                                                     "S", ("data/p/slash.particles", ""), "data/p/def.particles"), attackFailVfxOverrides=_table(
                                                     "F", ("data/p/fail.particles",))))],
                                    },
    "hd/character/enemy/obj1/animation/combined.timelines": {"timelines": {"cast": {"animationEvents": [
        {"type": "SpawnVFXConnected", "name": "beam", "eventTimestamp": 0.25, "duration": -1.0, "eventParams": [
            {"parameterName": "filename", "value": "data/p/beam.particles"}, {"parameterName": "attachToEntity", "value": True},
            {"parameterName": "sourceBoneName", "value": "elbow"}, {"parameterName": "targetBoneName", "value": "wrist"},
            {"parameterName": "hardKillOnTimeout", "value": False}]}]}}},
    "hd/items/weapon/axe/axe.json": {"type": "ItemDefinition", "name": "axe", "entities": [
        ent("e", comp("ObjectEffectDefinitionComponent", filename="data/p/axe_objfx.particles"))]},
    "hd/env/vis/dawn.json": {"type": "VisDefinition", "entities": [ent("vis_data", comp("VisualDataComponent", screenspaceVfx="data/p/motes.particles")),
                                                                  ent("other", comp("VisualDataComponent", screenspaceVfx=""))]},
    "hd/global/footprintdecals.json": {"dependencies": {"particles": [{"path": "data/p/manifest_only.particles"}]}, "parameters": [
        {"footprintId": "A", "vfxFile": "data/p/foot_a.particles"}, {"footprintId": "B", "vfxFile": "data/p/foot_b.particles"}]},
    "hd/env/weather/weather_effects.json": {"rain": "data/p/rain.particles", "snow": "data/p/snow.particles", "bubble2": "data/p/bubbles.particles"},
    "hd/global/excel/attackfxglobals.json": {"attackSuccessVfxBlood": _table("SB", ("data/p/g_slash.particles",)),
                                            "attackFailVfxBlood": {"enUS": _table("FB_en", (), "data/p/miss.particles"),
                                                                   "deDE": _table("FB_de", (), "data/p/miss_de.particles")}},
    "hd/global/excel/controllersettings.json": {"config": {"freeTargetVFXFilename": "data/p/reticle.particles"}},
    "hd/global/excel/gameplay_translation_settings.json": {"goldPickupVFXFilename": "data/p/gold.particles"},
    "hd/objects/vfx_only/town_portal.json": {"type": "UnitDefinition", "entities": [ent("entity_root", comp("VfxVariantComponent", vfx=[
        {"type": "VfxDefinitionComponent", "filename": "data/p/portal_a.particles"}, {"type": "VfxDefinitionComponent", "filename": "data/p/portal_b.particles"}]))]},
    "hd/objects/vfx_only/plain.json": {"type": "UnitDefinition", "entities": [ent("entity_root", vfx("data/p/plain.particles"))]},
}


class TestSurveyBindings(unittest.TestCase):
    """Mechanisms found by survey lane bind2: ObjectEffect, screenspaceVfx, attack success tables, global tables, SpawnVFXConnected."""

    @classmethod
    def setUpClass(cls):
        cls._td = tempfile.TemporaryDirectory()
        cls.root = Path(cls._td.name)
        for k, v in MORE.items():
            (cls.root / k).parent.mkdir(parents=True, exist_ok=True)
            (cls.root / k).write_text(json.dumps(v), encoding="utf-8")
        cls.fs = vfs.Layered([("t", cls.root)])

    @classmethod
    def tearDownClass(cls):
        cls._td.cleanup()

    def files(self, bs):
        return sorted(b.file.rsplit("/", 1)[-1] for b in bs)

    def test_paths(self):
        d = {"a": [{"b": 1}, {"b": 2}, {}], "c": {"d": "x"}}
        self.assertEqual(list(C._paths(d, "a[]/b")), [1, 2])
        self.assertEqual(list(C._paths(d, "c/d")), ["x"])
        self.assertEqual(list(C._paths(d, "a/b")) + list(C._paths(d, "c[]/d")) + list(C._paths(None, "x")), [])

    def test_object_effects(self):
        bs = C.object_effects(self.fs, "obj1")
        self.assertEqual([(b.kind, b.trigger, b.entity, b.file) for b in bs], [("objecteffect", "object", "entity_torso", "data/p/obj1_objfx.particles")])
        self.assertEqual(bs[0].extra["model"], "t.model")
        self.assertEqual(self.files(C.object_effects(self.fs, "hd/items/weapon/axe/axe.json")), ["axe_objfx.particles"])   # any definition path
        u = C.effects_of_unit(self.fs, "obj1")
        self.assertEqual([b.trigger for b in u.bindings if b.kind == "objecteffect"], ["object", "object"])
        self.assertEqual(self.files(u.vfx()), ["obj1_objfx.particles"])                    # drawn with the unit; empty file left out
        self.assertNotIn("never.particles", json.dumps(u.as_dict()))                         # dependencies.objecteffects is a manifest

    def test_attack_success(self):
        bs = C.attack_success_effects(self.fs, "obj1")
        self.assertEqual(self.files(bs), ["def.particles", "slash.particles"])              # fail table and empty rule file are not success
        self.assertEqual({(b.kind, b.trigger) for b in bs}, {("hitfx", "attack_success")})
        self.assertTrue(next(b for b in bs if b.file.endswith("def.particles")).extra["default"])
        self.assertEqual(next(b for b in bs if b.file.endswith("slash.particles")).extra["hit_classes"], 3)
        u = C.effects_of_unit(self.fs, "obj1")
        self.assertEqual(self.files([b for b in u.bindings if b.trigger == "attack_success"]), ["def.particles", "slash.particles"])
        self.assertNotIn("slash.particles", self.files(u.vfx()))                            # a hit effect plays on a hit, not with the unit

    def test_screenspace(self):
        for name in ("dawn", "hd/env/vis/dawn.json"):
            bs = C.screenspace_effects(self.fs, name)
            self.assertEqual([(b.trigger, b.entity, b.file, b.bone) for b in bs], [("screenspace", "vis_data", "data/p/motes.particles", None)])
        self.assertEqual(bs[0].extra["component"], "VisualDataComponent")

    def test_global_tables(self):
        bs = C.global_tables_effects(self.fs)
        self.assertEqual({b.trigger for b in bs}, {"global"})
        self.assertEqual(self.files(bs), sorted(["foot_a.particles", "foot_b.particles", "rain.particles", "snow.particles", "bubbles.particles",
                                                 "g_slash.particles", "miss.particles", "miss_de.particles", "reticle.particles", "gold.particles",
                                                 "portal_a.particles", "portal_b.particles", "plain.particles"]))   # manifest file left out
        kinds = {b.file.rsplit("/", 1)[-1]: b.extra for b in bs}
        self.assertEqual(kinds["foot_b.particles"]["key"], "parameters[]/vfxFile")
        self.assertEqual((kinds["miss_de.particles"]["lang"], kinds["miss_de.particles"]["key"]), ("deDE", "attackFailVfxBlood/deDE"))
        self.assertEqual(kinds["g_slash.particles"]["table_kind"], "attack_success")
        self.assertEqual([b.extra["variant"] for b in bs if b.file.startswith("data/p/portal")], [0, 1])
        self.assertEqual(self.files(C.attack_success_effects(self.fs)), ["g_slash.particles"])   # unit=None: the global success tables

    def test_missing_files_are_empty(self):
        with tempfile.TemporaryDirectory() as td:
            self.assertEqual(C.global_tables_effects(vfs.Layered([("e", Path(td))])), [])

    def test_spawn_vfx_connected(self):
        tl = C.effects_of_unit(self.fs, "obj1").timeline("sc")                     # the timeline "cast" is mode sc
        self.assertEqual(len(tl), 1)
        b = tl[0]
        self.assertEqual((b.kind, b.file, b.bone, b.attach, b.time, b.mode), ("vfx", "data/p/beam.particles", "elbow", True, 0.25, "sc"))
        self.assertEqual((b.extra["target_bone"], b.extra["event"], b.extra["duration"]), ("wrist", "SpawnVFXConnected", -1.0))
        self.assertEqual(len(C.effects_of_unit(self.fs, "obj1").vfx(mode="sc")), 2)

    def test_cli(self):
        def cli(*a):
            buf = io.StringIO()
            with redirect_stdout(buf):
                C.main(list(a) + ["--root", "t=%s" % self.root])
            return buf.getvalue()
        self.assertIn("motes.particles", cli("env", "dawn"))
        out = cli("global")
        self.assertIn("rain.particles", out)
        self.assertIn("key attackFailVfxBlood/deDE", out)
        self.assertEqual(len(json.loads(cli("global", "--json"))), 13)
        out = cli("unit", "obj1")
        self.assertIn("<object>", out)
        self.assertIn("<attack_success>", out)
        self.assertIn("<timeline sc @0.250s attached>", out)
        self.assertEqual(out.count("slash.particles"), 1)                                  # not repeated as a [hit] line
        with self.assertRaises(SystemExit):
            cli("env")


def _casc_fs():
    if not os.environ.get("HDKIT_CASC_GAME"):
        return None
    try:
        fs = vfs.Layered(vfs.parse_roots(["casc"]))
        if not fs.exists("hd/character/enemy/mephisto.json"):
            return None
        for unit in ("mephisto", "ancientbarb1", "diablo"):       # the files the tests read: a cache holding only some (no native
            C.effects_of_unit(fs, unit)                             # CascLib / StormLib to fetch the rest) means skip, not error
        C.screenspace_effects(fs, "act1_barracks_dawn1")
        C.global_tables_effects(fs)
        return fs
    except (Exception, SystemExit):    # noqa: BLE001 - no CASC storage / native libs
        return None


class TestRealCasc(unittest.TestCase):
    """Mephisto and the 12 ambient files on the game's own data (HDKIT_CASC_GAME set, `--root casc`); skipped without it."""

    @classmethod
    def setUpClass(cls):
        cls.fs = _casc_fs()
        if cls.fs is None:
            raise unittest.SkipTest("no CASC root")

    def test_mephisto(self):
        for name in ("mephisto", "ubermephisto"):                                         # the name rule applies to the unit
            u = C.effects_of_unit(self.fs, name)
            amb = [b for b in u.bindings if b.file.lower().endswith("vfx_mephisto_ambient.particles")]
            self.assertEqual([b.trigger for b in amb], ["name", "name"])
            self.assertTrue(all(b.bone is None and np.allclose(b.xf, np.eye(4)) for b in amb))
        tl = {(b.mode, b.file.rsplit("/", 1)[-1], b.bone, b.attach, round(b.time, 2)) for b in u.timeline()}
        self.assertIn(("dt", "vfx_mephisto_death.particles", "root", False, 0.1), tl)
        self.assertIn(("dt", "vfx_mephisto_death_objfx.particles", "special2", None, 0.0), tl)
        self.assertIn(("a2", "vfx_mephisto_attack2_objfx.particles", "special1", None, 0.0), tl)
        self.assertEqual(len(u.vfx(mode="dt")), len(u.vfx()) + 2)

    def test_survey_bindings(self):
        """bind2's mechanisms on the real data: an ObjectEffect unit and item, screenspace, success tables, global tables, a beam."""
        o = C.object_effects(self.fs, "ancientbarb1")
        self.assertEqual([(b.entity, b.trigger, b.file.rsplit("/", 1)[-1]) for b in o], [("entity_torso", "object", "vfx_ancientbarb1_objfx.particles")])
        self.assertTrue(C.object_effects(self.fs, "hd/items/weapon/axe/axe.json"))
        ss = C.screenspace_effects(self.fs, "act1_barracks_dawn1")
        self.assertEqual([b.file.rsplit("/", 1)[-1] for b in ss], ["vfx_act1_barracks_vis_motes.particles"])
        self.assertEqual({b.file.rsplit("/", 1)[-1] for b in C.attack_success_effects(self.fs, "arach1")},
                         {"vfx_AttackSuccess_Slash_Ichor.particles", "vfx_AttackSuccess_Point_Ichor.particles"})
        g = {b.file.rsplit("/", 1)[-1].lower() for b in C.global_tables_effects(self.fs)}
        for want in ("rain.particles", "snow.particles", "fx_blood_footprint.particles", "vfx_free_target_reticle.particles",
                     "vfx_console_gold_pickup.particles", "vfx_attackfail_miss_dede.particles", "vfx_town_portal_newstuff.particles"):
            self.assertIn(want, g)
        conn = [b for b in C.effects_of_unit(self.fs, "diablo").timeline("cast_loop") if b.extra["event"] == "SpawnVFXConnected"]
        self.assertTrue(conn and all(b.extra["target_bone"] and b.bone for b in conn))

    def test_ambient_files(self):
        want = {"doomknight2": ("R_weapon_attach", 4), "hydra1": ("spine_05SHJnt", 4)}
        for unit, (bone, n) in want.items():
            amb = [b for b in C.effects_of_unit(self.fs, unit).bindings if "ambient" in b.file.lower() and b.trigger == "entity"]
            self.assertGreaterEqual(len(amb), n)
            self.assertIn(bone, {b.bone for b in amb})
        ls = C.effects_of_unit(self.fs, "lightningspire")
        self.assertTrue(any("ambient_ball" in b.file for b in ls.bindings if b.bone is None))
        self.assertIsNotNone(C.resolve_particle(self.fs, "data/hd/vfx/particles/character/enemy/hydra1/vfx_hydra1_ambientFire.particles"))


@unittest.skipUnless(os.environ.get("HDFX_TEST_ROOT"), "no game data")
class TestRealData(unittest.TestCase):
    def test_fire_arrow_chain(self):
        root = os.environ["HDFX_TEST_ROOT"]
        fs = vfs.Layered([("g", Path(root))])
        m = C.effects_of_missile(fs, "firearrow")
        self.assertEqual(m.model, "fire_arrow")
        self.assertEqual(m.vfx()[0].position, (0.0, 5.0, 1.0))
        o = C.effects_of_overlay(fs, "apocalypse")
        self.assertTrue(o.path and o.vfx())


if __name__ == "__main__":
    unittest.main()
