"""Measured stage progress with a heartbeat for operations without a total."""
import re
import threading
import time
import sys
import shutil
from contextlib import contextmanager


@contextmanager
def copy_bytes(destination,total,label,notify,interval=3):
    """Observe actual local bytes during a blocking ADB pull."""
    import os
    from pathlib import Path
    destination=Path(destination); stop=threading.Event()
    def size():
        try:
            if destination.is_file():return destination.stat().st_size
            result=0
            for directory,_,files in os.walk(destination):
                for name in files:
                    try:result+=(Path(directory)/name).stat().st_size
                    except OSError:pass
            return result
        except OSError:return 0
    def message(done,complete=False):
        volume='%.1f MiB copied'%(done/1048576)
        if total:
            percent=100 if complete else min(99,done*100//total)
            return '[%3d%%] %s: %.1f / %.1f MiB'%(percent,label,done/1048576,total/1048576)
        return label+': '+volume
    notify(message(0))
    def monitor():
        previous=0
        while not stop.wait(interval):
            copied=size()
            if copied!=previous:
                previous=copied;notify(message(copied))
    thread=threading.Thread(target=monitor,daemon=True);thread.start()
    try:
        yield
    except BaseException:
        raise
    else:
        stop.set();thread.join()
        notify(message(size(),complete=True))
    finally:
        stop.set();thread.join()


@contextmanager
def decoder_progress(path,notify):
    stop=threading.Event()
    def monitor():
        previous=None
        while not stop.wait(1):
            try:
                with path.open('rb') as file:
                    file.seek(max(0,path.stat().st_size-16384))
                    text=file.read().decode('utf8',errors='replace')
                counts=re.findall(r'\[(\d+)/(\d+)\]',text)
                if counts and counts[-1]!=previous:
                    previous=counts[-1]
                    notify('Decoding resource packages: %s/%s'%previous)
            except OSError:
                pass
    thread=threading.Thread(target=monitor,daemon=True);thread.start()
    try:yield
    finally:stop.set();thread.join()


def error_details(error):
    from .config import CODE_ROOT
    path=CODE_ROOT/'reports/last-interface-error.txt'
    try:
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text(type(error).__name__+': '+str(error),encoding='utf8')
        return '%s. Details: %s'%(type(error).__name__,path)
    except OSError:
        return type(error).__name__+'. Check the source files and try again.'


def copy_tree(source,destination,notify):
    """Measure completed files, not an estimated completion time."""
    import shutil
    from pathlib import Path
    total=sum(path.is_file() for path in Path(source).rglob('*'))
    completed=0
    last=time.monotonic()
    label='Copying '+Path(source).name
    notify('%s: 0/%d'%(label,total))
    def copy_file(src,dst):
        nonlocal completed,last
        result=shutil.copy2(src,dst)
        completed+=1
        if completed==total or time.monotonic()-last>=1:
            notify('%s: %d/%d'%(label,completed,total));last=time.monotonic()
        return result
    return shutil.copytree(source,destination,copy_function=copy_file)


class ProgressDisplay:
    def __init__(self, output=print, interval=3):
        self.output=output; self.interval=interval
        self.stop=threading.Event(); self.lock=threading.Lock()
        self.message='Discovering source'; self.changed=time.monotonic()
        self.thread=None
        self.live=output is print and sys.stdout.isatty()
        self.width=0

    def _emit(self,message):
        if self.live:
            limit=max(1,shutil.get_terminal_size((80,24)).columns-1)
            message=message[:limit]
            sys.stdout.write('\r'+message.ljust(min(self.width,limit)))
            sys.stdout.flush();self.width=len(message)
        else:self.output(message)

    def __call__(self, message):
        with self.lock:
            self.message=str(message); self.changed=time.monotonic()
            self._emit(self.format(self.message))

    @staticmethod
    def format(message):
        if re.match(r'^\[\s*\d+%\]',message):return message
        match=re.search(r'(\d+)/(\d+)',message)
        if match and int(match[2]):
            return '[%3d%%] %s'%(min(100,int(match[1])*100//int(match[2])),message)
        return '[working] '+message

    def _heartbeat(self):
        while not self.stop.wait(self.interval):
            with self.lock:
                elapsed=time.monotonic()-self.changed
                if elapsed>=self.interval:
                    self._emit(self.format(self.message)+' (%ds since last update)'%elapsed)

    def __enter__(self):
        self.thread=threading.Thread(target=self._heartbeat,daemon=True)
        self.thread.start()
        return self

    def __exit__(self,*args):
        self.stop.set()
        self.thread.join()
        if self.live and self.width:
            sys.stdout.write('\n');sys.stdout.flush()
