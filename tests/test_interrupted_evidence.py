"""Retain visible context from real interrupted social-site checks."""

import pytest

from app.core.health import result_image
from app.core.models import CheckRun


@pytest.mark.parametrize("run_id", [26, 27, 29])
def test_interrupted_checks_show_last_real_frame_without_claiming_pass(run_id):
    result = CheckRun.query.get(run__id=run_id)
    assert result.status == "failure" and not result.passed
    image, label = result_image(result)
    assert image == f"/evidence/{run_id}/live.jpg"
    assert label == "Last live frame · check incomplete"
