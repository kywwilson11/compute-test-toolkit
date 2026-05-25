import pytest

from computetest import ethernet, gmsl, gpu, nvme


@pytest.mark.parametrize("good,bad", [
    (lambda: nvme.check_nvme("/dev/nvme0"), lambda: nvme.check_nvme("/dev/nvme_BAD")),
    (lambda: gpu.check_gpu(0), lambda: gpu.check_gpu(99)),
    (lambda: gmsl.check_gmsl("1-0029"), lambda: gmsl.check_gmsl("1-BAD")),
    (lambda: ethernet.check_ethernet("eth0"), lambda: ethernet.check_ethernet("ethBAD")),
    (lambda: ethernet.check_can("can0"), lambda: ethernet.check_can("canBAD")),
])
def test_good_passes_bad_fails(good, bad):
    assert good().ok is True
    assert bad().ok is False


def test_nvme_limits_catch_media_errors():
    h = nvme.check_nvme("/dev/nvme_BAD")
    assert h.checks["media_errors==0"] is False
    assert h.smart["media_errors"] > 0


def test_gpu_catches_uncorrected_ecc_and_throttle():
    h = gpu.check_gpu(99)
    assert h.checks["ecc_uncorrected==0"] is False
    assert h.checks["no_thermal_throttle"] is False


def test_can_bus_off_detected():
    h = ethernet.check_can("canBAD")
    assert h.state == "BUS-OFF"
    assert h.checks["not_bus_off"] is False
