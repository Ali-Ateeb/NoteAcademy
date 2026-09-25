"""The public crop bucket must equal "crops of approved questions".

The rule that matters is the negative one: nothing that is not approved may be
left in a bucket the world can read.
"""

from noteacademy_pipeline.public_crops import apply_plan, plan_sync
from noteacademy_pipeline.storage import StorageError


def keys(*names):
    return {n: f'"{n}"' for n in names}


class TestPlan:
    def test_an_approved_crop_not_yet_public_is_copied(self):
        plan = plan_sync({"a"}, keys("a"), {})
        assert plan.to_copy == ["a"] and plan.to_remove == []

    def test_a_current_crop_is_left_alone(self):
        plan = plan_sync({"a"}, keys("a"), keys("a"))
        assert plan.to_copy == [] and plan.unchanged == 1

    def test_a_re_rendered_crop_is_copied_again(self):
        plan = plan_sync({"a"}, {"a": "new"}, {"a": "old"})
        assert plan.to_copy == ["a"]

    def test_an_unknown_etag_is_treated_as_changed_not_as_current(self):
        assert plan_sync({"a"}, {"a": None}, {"a": None}).to_copy == ["a"]
        assert plan_sync({"a"}, {"a": "x"}, {"a": None}).to_copy == ["a"]

    def test_a_public_crop_that_is_no_longer_approved_is_removed(self):
        plan = plan_sync({"a"}, keys("a", "b"), keys("a", "b"))
        assert plan.to_remove == ["b"]

    def test_a_public_crop_with_no_approved_owner_at_all_is_removed(self):
        plan = plan_sync(set(), keys(), keys("orphan"))
        assert plan.to_remove == ["orphan"]

    def test_an_unapproved_crop_is_never_copied_even_though_it_exists_privately(self):
        plan = plan_sync({"a"}, keys("a", "unapproved"), {})
        assert plan.to_copy == ["a"]

    def test_an_approved_crop_missing_from_the_private_bucket_is_reported_not_copied(self):
        plan = plan_sync({"gone"}, {}, {})
        assert plan.missing_source == ["gone"] and plan.to_copy == []

    def test_running_the_plan_twice_leaves_nothing_to_do(self):
        approved = {"a", "b"}
        private = keys("a", "b", "c")
        public = {}
        first = plan_sync(approved, private, public)
        public.update({k: private[k] for k in first.to_copy})
        second = plan_sync(approved, private, public)
        assert second.to_copy == [] and second.to_remove == []


class FakeStorage:
    def __init__(self, bucket, objects=None, fail=()):
        self.bucket = bucket
        self.objects = dict(objects or {})
        self.fail = set(fail)
        self.removed = []

    def download(self, key):
        if key in self.fail:
            raise StorageError(f"boom {key}")
        return self.objects[key]

    def put(self, key, data, content_type, cache_control):
        assert cache_control  # never store a public crop without a cache lifetime
        self.objects[key] = data

    def remove(self, batch):
        self.removed.extend(batch)
        for key in batch:
            self.objects.pop(key, None)


class TestApply:
    def test_copies_and_removes_what_the_plan_says(self):
        private = FakeStorage("priv", {"a": b"A", "b": b"B"})
        public = FakeStorage("pub", {"stale": b"S"})
        plan = plan_sync({"a", "b"}, keys("a", "b"), keys("stale"))
        report = apply_plan(private, public, plan, workers=2)
        assert report.copied == 2 and report.removed == 1 and not report.failed
        assert set(public.objects) == {"a", "b"}

    def test_one_failed_copy_does_not_stop_the_others_and_is_reported(self):
        private = FakeStorage("priv", {"a": b"A", "b": b"B"}, fail={"a"})
        public = FakeStorage("pub")
        plan = plan_sync({"a", "b"}, keys("a", "b"), {})
        report = apply_plan(private, public, plan, workers=2)
        assert report.copied == 1 and report.failed == ["a"]
        assert "b" in public.objects and "a" not in public.objects
