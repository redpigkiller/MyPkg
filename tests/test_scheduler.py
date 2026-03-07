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

def test_job_retry():
    attempts = 0
    def flaky_func():
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise ValueError("Not yet")
        return "success"

    job = FuncJob("flaky", flaky_func, max_retries=3)
    with JobManager(max_workers=1) as manager:
        manager.add(job)
        manager.wait()
        
    assert job.status == DONE
    assert job.result == "success"
    assert attempts == 3

def test_job_callbacks():
    log_called = False
    done_called = False
    fail_called = False
    watch_called = False
    
    job = CmdJob("cb_job", "echo Hello World")
    
    def on_log(j, line):
        nonlocal log_called
        if "Hello" in line:
            log_called = True
            
    def on_done(j):
        nonlocal done_called
        done_called = True
        
    def on_fail(j, e):
        nonlocal fail_called
        fail_called = True
        
    def on_watch(j, m):
        nonlocal watch_called
        watch_called = True
    
    job.on_log(on_log)
    job.on_done(on_done)
    job.on_fail(on_fail)
    job.watch("World", on_watch)
    
    with JobManager(max_workers=1) as manager:
        manager.add(job)
        manager.wait()
        
    assert job.status == DONE
    assert log_called
    assert done_called
    assert not fail_called
    assert watch_called

def test_manager_pause_resume():
    job1 = FuncJob("j1", lambda: time.sleep(0.1))
    job2 = FuncJob("j2", lambda: time.sleep(0.1))
    
    with JobManager(max_workers=1) as manager:
        manager.pause()
        manager.add(job1)
        manager.add(job2)
        
        time.sleep(0.1)
        assert len(manager.pending()) == 2
        assert len(manager.running()) == 0
        
        manager.resume()
        manager.wait()
        
    assert job1.status == DONE
    assert job2.status == DONE

def test_cmd_job_failure():
    # Run a command that definitely fails
    job = CmdJob("fail_job", "exit 1")
    with JobManager(max_workers=1) as manager:
        manager.add(job)
        manager.wait()
        
    assert job.status == FAILED
    assert job.result != 0

def test_on_queue_drained():
    drained = False
    def on_drained(mgr):
        nonlocal drained
        drained = True
        
    with JobManager(max_workers=1) as manager:
        manager.on_queue_drained(on_drained)
        manager.add(CmdJob("quick", "echo 1"))
        manager.wait()
        
    # Wait slightly for event bus to fire
    time.sleep(0.1)
    assert drained

def test_manager_cancel_all():
    job1 = FuncJob("j1", lambda: time.sleep(0.5))
    job2 = FuncJob("j2", lambda: time.sleep(0.5))
    
    with JobManager(max_workers=1) as manager:
        manager.add(job1)
        manager.add(job2)
        time.sleep(0.1) # Let j1 start
        manager.cancel_all()
        manager.wait()
        
    assert job1.status == CANCELLED
    assert job2.status == CANCELLED

