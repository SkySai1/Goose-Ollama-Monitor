"""Optional real-Chrome smoke test. No npm/Python dependencies.

Run a monitor on :11436, then: python3 tests/browser_smoke.py
Uses an isolated temporary Chrome profile and the DevTools pipe.
"""
import base64
import html
import json
import os
from pathlib import Path
import select
import subprocess
import tempfile
import time
from urllib.request import urlopen

CHROME = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'


class Browser:
    def __init__(self, executable=CHROME, env=None, existing_page=False):
        self.profile = tempfile.TemporaryDirectory(prefix='ollama-monitor-chrome-')
        child_in, self.writer = os.pipe()
        self.reader, child_out = os.pipe()
        # Preserve both descriptors before mapping them onto CDP's required 3/4.
        import fcntl
        child_in_copy = fcntl.fcntl(child_in, fcntl.F_DUPFD, 10)
        child_out_copy = fcntl.fcntl(child_out, fcntl.F_DUPFD, 10)
        def setup():
            os.dup2(child_in_copy, 3)
            os.dup2(child_out_copy, 4)
        self.process = subprocess.Popen([executable, '--headless=new', '--no-first-run',
            '--no-default-browser-check', '--disable-background-networking',
            '--remote-debugging-pipe', '--user-data-dir='+self.profile.name, 'about:blank'],
            preexec_fn=setup, close_fds=False, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for fd in [child_in, child_out, child_in_copy, child_out_copy]: os.close(fd)
        self.buffer = b''
        self.counter = 0
        self.errors = []
        try:
            if existing_page:
                target = None
                for _ in range(100):
                    pages = [t for t in self.call('Target.getTargets')['targetInfos']
                             if t['type']=='page' and 'index.html' in t['url']]
                    if pages:
                        target = pages[0]['targetId']; break
                    time.sleep(.1)
                if not target: raise RuntimeError('No application page')
            else:
                target = self.call('Target.createTarget', {'url':'about:blank'})['targetId']
            self.session = self.call('Target.attachToTarget', {'targetId':target,'flatten':True})['sessionId']
            self.call('Runtime.enable', session=True)
            self.call('Page.enable', session=True)
        except Exception:
            self.close()
            raise

    def call(self, method, params=None, session=False):
        self.counter += 1
        request = {'id':self.counter,'method':method,'params':params or {}}
        if session: request['sessionId'] = self.session
        os.write(self.writer, json.dumps(request).encode()+b'\0')
        deadline = time.monotonic()+20
        while time.monotonic()<deadline:
            if b'\0' not in self.buffer:
                ready, _, _ = select.select([self.reader], [], [], 1)
                if not ready: continue
                chunk = os.read(self.reader, 1024*1024)
                if not chunk: raise RuntimeError('Chrome pipe closed')
                self.buffer += chunk
            while b'\0' in self.buffer:
                raw, self.buffer = self.buffer.split(b'\0',1)
                response = json.loads(raw)
                if response.get('method') == 'Runtime.exceptionThrown': self.errors.append(response['params'])
                if response.get('id') == self.counter:
                    if 'error' in response: raise RuntimeError(response['error'])
                    return response.get('result',{})
        raise TimeoutError(method)

    def js(self, expression):
        result = self.call('Runtime.evaluate', {'expression':expression,'returnByValue':True,'awaitPromise':True}, session=True)
        if 'exceptionDetails' in result: raise RuntimeError(result['exceptionDetails'])
        return result['result'].get('value')

    def wait_for(self, expression):
        for _ in range(50):
            if self.js(expression): return
            time.sleep(.1)
        raise AssertionError('Timed out: '+expression)

    def close(self):
        self.process.terminate()
        try: self.process.wait(timeout=5)
        except subprocess.TimeoutExpired: self.process.kill();self.process.wait()
        os.close(self.writer);os.close(self.reader)
        self.profile.cleanup()


def main():
    browser = Browser()
    try:
        browser.call('Emulation.setDeviceMetricsOverride', {'width':880,'height':940,'deviceScaleFactor':1,'mobile':False}, session=True)
        browser.call('Page.navigate', {'url':'http://127.0.0.1:11436'}, session=True)
        browser.wait_for("document.getElementById('collector')?.textContent==='ONLINE'")
        assert browser.js("document.querySelectorAll('canvas').length") == 4
        for selected_range in ['1m','1h','15m']:
            browser.js("document.querySelector('[data-range=\""+selected_range+"\"]').click()")
            browser.wait_for("history?.range==='"+selected_range+"'")
            assert browser.js("document.querySelectorAll('[aria-pressed=true]').length") == 1
        browser.js("metrics[0].canvas.dispatchEvent(new KeyboardEvent('keydown',{key:'ArrowLeft',bubbles:true}))")
        assert browser.js("!document.getElementById('tooltip').hidden")
        assert 'System RAM' in browser.js("document.getElementById('tooltip').textContent")
        browser.js('clearHover()')
        browser.js("window.savedFetch=window.fetch;window.fetch=async()=>{throw new Error('test outage')};clearTimeout(timer);poll()")
        browser.wait_for("document.getElementById('collector').textContent==='UNAVAILABLE'")
        assert browser.js("document.getElementById('value-0').textContent") == '—'
        browser.js('window.fetch=window.savedFetch;clearTimeout(timer);poll()')
        browser.wait_for("document.getElementById('collector').textContent==='ONLINE'")
        shot = browser.call('Page.captureScreenshot', {'format':'png','captureBeyondViewport':True}, session=True)
        Path('/tmp/ollama-monitor-live.png').write_bytes(base64.b64decode(shot['data']))
        # Isolated, explicitly synthetic series exercise one hour, gaps, and zero values.
        browser.js('''clearTimeout(timer);controller?.abort();revision++;
        history={range:'1h',start:Date.now()/1000-3600,end:Date.now()/1000,interval_seconds:2,samples:[]};
        for(let i=0;i<=1800;i++)history.samples.push({timestamp:history.start+i*2,
          system_ram_percent:45+15*Math.sin(i/150),system_ram_bytes:25*1024**3,system_total_bytes:48*1024**3,
          ollama_ram_bytes:(12+4*Math.sin(i/200))*1024**3,ollama_ram_percent:30,
          memory_pressure_percent:i>700&&i<800?null:20+10*Math.sin(i/120),memory_free_percent:75,
          context_percent:i>1200&&i<1300?null:i%100,context_used_tokens:i%100*1000,context_max_tokens:100000,
          context_source:'test fixture',context_runner_pid:5,context_slot:0,context_model:'fixture'});
        drawAll();''')
        assert browser.js("metrics.every(m=>m.empty.hidden)")
        browser.call('Emulation.setDeviceMetricsOverride', {'width':390,'height':844,'deviceScaleFactor':2,'mobile':False}, session=True)
        browser.js('drawAll()')
        assert browser.js('document.documentElement.scrollWidth <= innerWidth')
        shot = browser.call('Page.captureScreenshot', {'format':'png','captureBeyondViewport':True}, session=True)
        Path('/tmp/ollama-monitor-narrow-fixture.png').write_bytes(base64.b64decode(shot['data']))
        # Reproduce Goose's opaque sandbox with connect-src 'none'. The app must
        # initialize the MCP Apps bridge and recover through its read-only tool.
        with urlopen('http://127.0.0.1:11436/api/status') as response:
            status = json.load(response)
        with urlopen('http://127.0.0.1:11436/api/history?range=15m') as response:
            data = json.load(response)
        app = (Path(__file__).resolve().parents[1] / 'app/ollama-monitor.html').read_text()
        app = app.replace('<head>', '''<head><meta http-equiv="Content-Security-Policy"
            content="default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'none'">''')
        app += '''<script>setInterval(()=>parent.postMessage({probe:document.getElementById('collector').textContent,banner:document.getElementById('banner').textContent},'*'),100)</script>'''
        host = '''<div id="probe"></div><div id="diagnostic"></div><script>window.toolCalls=0;window.initCalls=0;window.backendDown=false;
        const readings=READINGS;
        addEventListener('message',event=>{
          const m=event.data;
          if(m.probe){document.getElementById('probe').textContent=m.probe;document.getElementById('diagnostic').textContent=m.banner;return;}
          if(!m.id||!m.method)return;
          let result={};
          if(m.method==='ui/initialize'){
            window.initCalls++;
            if(window.initCalls===1){event.source.postMessage({jsonrpc:'2.0',id:m.id,error:{code:-32000,message:'Session still starting'}},'*');return;}
            result={protocolVersion:'2025-11-21',hostInfo:{name:'test',version:'1'},hostCapabilities:{},hostContext:{}};
          }
          if(m.method==='tools/call'){
            window.toolCalls++;readings.status.timestamp=Date.now()/1000;
            result=window.backendDown?{isError:true,content:[{type:'text',text:'Test backend offline'}]}:{structuredContent:readings,content:[]};
          }
          // Request and response IDs are independent in each direction.
          event.source.postMessage({jsonrpc:'2.0',id:m.id,method:'ping'},'*');
          setTimeout(()=>event.source.postMessage({jsonrpc:'2.0',id:m.id,result},'*'),100);
        });</script>'''.replace('READINGS', json.dumps({'status':status,'history':data}))
        host += '<iframe sandbox="allow-scripts" srcdoc="'+html.escape(app, quote=True)+'"></iframe>'
        browser.call('Page.navigate', {'url':'about:blank'}, session=True)
        frame = browser.call('Page.getFrameTree', session=True)['frameTree']['frame']['id']
        browser.call('Page.setDocumentContent', {'frameId':frame,'html':host}, session=True)
        browser.wait_for("document.getElementById('probe')?.textContent==='UNAVAILABLE'")
        assert 'Goose bridge: Session still starting' in browser.js("document.getElementById('diagnostic').textContent")
        browser.wait_for("document.getElementById('probe')?.textContent==='ONLINE'")
        assert browser.js('window.initCalls >= 2')
        assert browser.js('window.toolCalls > 0')
        browser.js('window.backendDown=true')
        browser.wait_for("document.getElementById('probe')?.textContent==='UNAVAILABLE'")
        assert 'Monitoring backend: Test backend offline' in browser.js("document.getElementById('diagnostic').textContent")
        browser.js('window.backendDown=false')
        browser.wait_for("document.getElementById('probe')?.textContent==='ONLINE'")
        assert not browser.errors, browser.errors
        print('PASS: real API, four charts, ranges, keyboard hover, offline/reconnect, 1801-point gaps, narrow layout, Goose sandbox bridge, no JS exceptions')
        print('Screenshots: /tmp/ollama-monitor-live.png; /tmp/ollama-monitor-narrow-fixture.png (synthetic history)')
    finally:
        browser.close()


if __name__ == '__main__':
    main()
