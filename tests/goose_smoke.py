"""Installed Goose integration, temporary config/profile, no LLM prompts.

Requires macOS Goose and a running monitor on 11436. Opens a temporary Goose
window, calls open_monitor via Goose's MCP client, then verifies the app bridge.
"""
import base64
import json
import os
from pathlib import Path
import sys
import tempfile
import time
from browser_smoke import Browser

ROOT = Path(__file__).resolve().parents[1]
GOOSE = '/Applications/Goose.app/Contents/MacOS/Goose'


def main():
    launcher = "--launcher" in sys.argv
    with tempfile.TemporaryDirectory(prefix='ollama-goose-test-') as folder:
        config = Path(folder)/'config'
        config.mkdir()
        # Paths are JSON-quoted strings, also valid YAML scalar strings.
        (config/'config.yaml').write_text('GOOSE_PROVIDER: ollama\nGOOSE_MODEL: qwen3.6:35b\nOLLAMA_HOST: localhost\nextensions:\n  ollamamonitor:\n    enabled: true\n    type: stdio\n    name: Ollama Monitor\n    cmd: '+json.dumps(sys.executable)+'\n    args:\n      - '+json.dumps(str(ROOT/'ollama-monitor-mcp.py'))+'\n    envs: {}\n    env_keys: []\n    timeout: 15\n')
        if launcher:
            sys.path.insert(0, str(ROOT))
            from unittest.mock import patch
            from ollama_monitor.goose import install_launcher
            with patch.dict(os.environ, {'GOOSE_PATH_ROOT':folder}):
                install_launcher()
        b = Browser(executable=GOOSE, env={**os.environ, 'GOOSE_PATH_ROOT':folder}, existing_page=True)
        try:
            b.wait_for('typeof window.appConfig!=="undefined"')
            time.sleep(2)
            # Decline telemetry in the disposable profile only.
            b.js("[...document.querySelectorAll('button')].find(x=>/Нет, спасибо|No, thanks/i.test(x.textContent))?.click()")
            # Discover the installed renderer entry, rather than pinning a bundle hash.
            if launcher:
                b.js("location.hash='/apps'")
                b.wait_for("[...document.querySelectorAll('h3')].some(e=>e.textContent==='Ollama Monitor')")
                # Re-open the page to check that the persisted card survives refresh.
                b.call('Page.reload', session=True)
                b.wait_for("[...document.querySelectorAll('h3')].some(e=>e.textContent==='Ollama Monitor')")
                b.js("[...document.querySelectorAll('h3')].find(e=>e.textContent==='Ollama Monitor').parentElement.parentElement.querySelector('button').click()")
            else:
                result = b.js('''(async()=>{
                  const scripts=[...document.scripts].map(s=>s.src);
                  const entry=scripts.find(src=>/assets\\/index-.*\\.js/.test(src));
                  const sdk=await import(entry);
                  // Goose 1.52 exports these functions from its renderer SDK bundle.
                  const {sessionId}=await sdk.Hn('/tmp');
                  const client=await sdk.wi();
                  const result=await client.goose.toolsCall_unstable({sessionId,extensionName:'ollamamonitor',name:'ollamamonitor__open_monitor',arguments:{}});
                  const apps=await sdk.qi(sessionId);
                  const app=apps.find(a=>a.uri==='ui://ollama-monitor/dashboard');
                  if(!app)throw new Error('MCP app was not discovered');
                  await window.electron.launchApp(app);
                  return {meta:result._meta,isError:result.isError,app:{width:app.width,height:app.height,resizable:app.resizable}};
                })()''')
                assert not result.get('isError'), result
                assert result['meta']['ui']['resourceUri']=='ui://ollama-monitor/dashboard', result
                assert result['app']=={'width':880,'height':940,'resizable':True}, result
            # Attach to the actual guest iframe containing the monitor.
            found = False
            for _ in range(80):
                for target in b.call('Target.getTargets')['targetInfos']:
                    if target['type']!='iframe': continue
                    b.session=b.call('Target.attachToTarget',{'targetId':target['targetId'],'flatten':True})['sessionId']
                    if b.js("!!document.getElementById('collector')"):
                        found=True; break
                if found: break
                time.sleep(.2)
            assert found, 'Goose did not render the app iframe'
            b.wait_for("document.getElementById('collector').textContent==='ONLINE'")
            assert b.js('bridgeReady!==null')
            for range_name in ['1m','1h']:
                b.js("document.querySelector('[data-range=\""+range_name+"\"]').click()")
                b.wait_for("history?.range==='"+range_name+"'")
            app_window=next(t for t in b.call('Target.getTargets')['targetInfos']
                            if t['type']=='page' and 'standalone-app' in t['url'])
            b.session=b.call('Target.attachToTarget',{'targetId':app_window['targetId'],'flatten':True})['sessionId']
            screenshot=b.call('Page.captureScreenshot', {'format':'png'}, session=True)
            Path('/tmp/ollama-monitor-goose.png').write_bytes(base64.b64decode(screenshot['data']))
            print('PASS:', 'Apps card → Launch without chat' if launcher else 'MCP open_monitor', '→ installed Goose window ONLINE, 1m/1h ranges')
        finally:
            b.close()


if __name__=='__main__':
    main()
