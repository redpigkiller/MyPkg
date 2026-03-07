import time
import pytest
from mypkg.scheduler import JobManager, CmdJob, FuncJob, DONE, FAILED, CANCELLED

def test_cmd_job():
    job = CmdJob("test_cmd", "echo hello")
    with JobManager(max_workers=1) as manager:
        manager.add(job)
        manager.wait()
    
    assert job.status == DONE
    assert job.result == 0
    assert "hello" in "\n".join(job.logs())

def test_func_job():
    def my_func(a, b):
        return a + b

    job = FuncJob("test_func", my_func, args=(1, 2))
    with JobManager(max_workers=1) as manager:
        manager.add(job)
        manager.wait()

    assert job.status == DONE
    assert job.result == 3

def test_job_cancel():
    def long_func():
        time.sleep(1)
        return "yes"

    job = FuncJob("long", long_func)
    with JobManager(max_workers=2) as manager:
        manager.add(job)
        time.sleep(0.1)
        manager.cancel(job.id)
        manager.wait()

    assert job.status == CANCELLED
    
def test_dynamic_resources():
    flag = {"gpu": 0}
    
    def my_func():
        return "ok"

    job = FuncJob("test", my_func, resources={"gpu": 1})
    with JobManager(max_workers=2, resources={"gpu": lambda: flag["gpu"]}) as manager:
        manager.add(job)
        time.sleep(0.2)
        assert job.status == "pending" # Resource 0, can't fit
        
        flag["gpu"] = 1 # give resource
        manager.wait(job.id, timeout=2.0)
        assert job.status == DONE
