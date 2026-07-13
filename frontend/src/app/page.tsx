'use client';

import { useState, useEffect } from 'react';

export default function Home() {
  const [provider, setProvider] = useState('gpt-4o');
  const [keys, setKeys] = useState({
    openai: '',
    anthropic: '',
    gemini: '',
    glm: ''
  });
  
  const [brandVoice, setBrandVoice] = useState('');
  const [postCount, setPostCount] = useState(10);
  
  const [status, setStatus] = useState<'idle' | 'pending' | 'running' | 'success' | 'error'>('idle');
  const [taskId, setTaskId] = useState('');
  const [calendarData, setCalendarData] = useState<any[]>([]);
  const [csvOutput, setCsvOutput] = useState('');
  const [message, setMessage] = useState('');

  useEffect(() => {
    setKeys({
      openai: localStorage.getItem('sm_openai_key') || '',
      anthropic: localStorage.getItem('sm_anthropic_key') || '',
      gemini: localStorage.getItem('sm_gemini_key') || '',
      glm: localStorage.getItem('sm_glm_key') || ''
    });

    let interval: NodeJS.Timeout;
    if (taskId && (status === 'pending' || status === 'running')) {
      interval = setInterval(async () => {
        try {
          const res = await fetch(`http://localhost:8008/api/tasks/${taskId}`);
          if (res.ok) {
            const data = await res.json();
            setStatus(data.status);
            if (data.status === 'success') {
              setCalendarData(data.calendar_data || []);
              setCsvOutput(data.csv_output);
              setMessage('Content calendar generated successfully!');
            } else if (data.status === 'error') {
              setMessage('Error generating calendar.');
            }
          }
        } catch (e) {
          console.error("Polling error", e);
        }
      }, 3000);
    }
    return () => clearInterval(interval);
  }, [taskId, status]);

  const handleKeyChange = (provider: string, val: string) => {
    setKeys(prev => ({...prev, [provider]: val}));
  };

  const handleExecute = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!brandVoice) return;

    setStatus('pending');
    setMessage(`Generating ${postCount} posts using ${provider}...`);
    setCalendarData([]);
    setCsvOutput('');
    
    try {
      localStorage.setItem('sm_openai_key', keys.openai);
      localStorage.setItem('sm_anthropic_key', keys.anthropic);
      localStorage.setItem('sm_gemini_key', keys.gemini);
      localStorage.setItem('sm_glm_key', keys.glm);

      const res = await fetch('http://localhost:8008/api/execute', {
        method: 'POST',
        headers: { 
          'Content-Type': 'application/json',
          'X-OpenAI-Key': keys.openai,
          'X-Anthropic-Key': keys.anthropic,
          'X-Gemini-Key': keys.gemini,
          'X-GLM-Key': keys.glm,
        },
        body: JSON.stringify({
          brand_voice: brandVoice,
          post_count: postCount,
          model_id: provider
        }),
      });
      
      const data = await res.json();
      if (res.ok) {
        setTaskId(data.task_id);
      } else {
        setStatus('error');
        setMessage('Failed to start task.');
      }
    } catch (e) {
      console.error(e);
      setStatus('error');
      setMessage('Network error.');
    }
  };

  const handleDownloadCsv = () => {
    if (!csvOutput) return;
    const blob = new Blob([csvOutput], { type: 'text/csv;charset=utf-8;' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.setAttribute('download', 'hootsuite_bulk_upload.csv');
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
  };

  return (
    <main className="dashboard-container">
      <div className="dashboard-header">
        <h1>Social Media Agent</h1>
        <p style={{color: '#6b7280', marginTop: '10px', fontSize: '1.1rem'}}>Autonomous Multi-LLM Content Calendar & Hootsuite Exporter</p>
      </div>

      <div style={{display: 'flex', gap: '30px', flexWrap: 'wrap'}}>
        <div style={{flex: '1 1 400px'}}>
          <div className="panel">
            <h2 className="panel-title">Universal API Gateway</h2>
            
            <div className="form-group">
              <label>OpenAI API Key</label>
              <input type="password" value={keys.openai} onChange={(e) => handleKeyChange('openai', e.target.value)} />
            </div>
            <div className="form-group">
              <label>Anthropic API Key</label>
              <input type="password" value={keys.anthropic} onChange={(e) => handleKeyChange('anthropic', e.target.value)} />
            </div>
            <div className="form-group">
              <label>Google AI Key</label>
              <input type="password" value={keys.gemini} onChange={(e) => handleKeyChange('gemini', e.target.value)} />
            </div>
            <div className="form-group">
              <label>ZhipuAI Key</label>
              <input type="password" value={keys.glm} onChange={(e) => handleKeyChange('glm', e.target.value)} />
            </div>

            <div className="form-group" style={{marginTop: '30px'}}>
              <label style={{color: 'var(--primary)', fontWeight: 'bold'}}>Active LLM Router (litellm)</label>
              <select value={provider} onChange={(e) => setProvider(e.target.value)} disabled={status === 'pending' || status === 'running'} style={{border: '2px solid var(--primary)'}}>
                <option value="claude-3-5-sonnet-20240620">Anthropic (claude-3-5-sonnet) - Best for Copywriting</option>
                <option value="gpt-4o">OpenAI (gpt-4o)</option>
                <option value="gemini/gemini-1.5-pro">Google AI (gemini-1.5-pro)</option>
                <option value="zhipu/glm-4">ZhipuAI (glm-4)</option>
                <option value="ollama/llama3">Ollama (llama3 - local)</option>
              </select>
            </div>
          </div>

          <div className="panel">
            <h2 className="panel-title">Content Engine</h2>
            <form onSubmit={handleExecute}>
              <div className="form-group">
                <label>Number of Posts to Generate</label>
                <input 
                  type="number" 
                  value={postCount} 
                  onChange={(e) => setPostCount(parseInt(e.target.value))} 
                  min={1} max={50}
                  required
                  disabled={status === 'pending' || status === 'running'}
                />
              </div>
              <div className="form-group">
                <label>Brand Voice, Guidelines & Topics</label>
                <textarea 
                  value={brandVoice} 
                  onChange={(e) => setBrandVoice(e.target.value)} 
                  placeholder="e.g. Witty, professional, focused on B2B SaaS growth. Post about AI, tech trends, and productivity."
                  rows={5}
                  required
                  disabled={status === 'pending' || status === 'running'}
                />
              </div>
              <button type="submit" className="btn" style={{width: '100%'}} disabled={status === 'pending' || status === 'running'}>
                {status === 'pending' || status === 'running' ? 'Writing Content...' : 'Generate Content Calendar'}
              </button>
            </form>
          </div>

          {status !== 'idle' && status !== 'success' && (
            <div className={`status-message ${status}`}>
              {message}
            </div>
          )}
        </div>

        <div style={{flex: '2 1 600px'}}>
          <div className="panel" style={{height: '100%', display: 'flex', flexDirection: 'column'}}>
            <h2 className="panel-title">Calendar Viewer</h2>
            
            <div style={{flex: 1, overflowY: 'auto', maxHeight: '600px', background: '#ffffff'}}>
              {status === 'idle' ? (
                <p style={{color: '#9ca3af', textAlign: 'center', marginTop: '60px'}}>No calendar generated yet.</p>
              ) : status === 'running' || status === 'pending' ? (
                <div style={{textAlign: 'center', marginTop: '60px'}}>
                  <div style={{width: '50px', height: '50px', border: '4px solid #e5e7eb', borderTopColor: 'var(--primary)', borderRadius: '50%', animation: 'spin 1s linear infinite', margin: '0 auto'}}></div>
                  <style>{`@keyframes spin { 0% { transform: rotate(0deg); } 100% { transform: rotate(360deg); } }`}</style>
                  <p style={{marginTop: '20px', fontWeight: 'bold', color: 'var(--primary)'}}>Architecting {postCount} posts...</p>
                </div>
              ) : calendarData && calendarData.length > 0 ? (
                <div>
                  {calendarData.map((post, i) => (
                    <div key={i} className="post-card">
                      <div className="post-date">🕒 Scheduled: {post.date}</div>
                      <div className="post-content">{post.message}</div>
                      {post.url && <div style={{marginTop: '10px', fontSize: '0.85rem'}}><a href={post.url} target="_blank" style={{color: '#2563eb'}}>{post.url}</a></div>}
                    </div>
                  ))}
                </div>
              ) : (
                <p>Failed to parse calendar.</p>
              )}
            </div>
            
            {status === 'success' && csvOutput && (
              <div style={{marginTop: '20px'}}>
                <button onClick={handleDownloadCsv} className="btn btn-secondary" style={{width: '100%'}}>
                  Download Hootsuite Bulk CSV
                </button>
                <p style={{fontSize: '0.8rem', color: '#6b7280', marginTop: '10px', textAlign: 'center'}}>
                  Upload this CSV to Hootsuite, Buffer, or Meta Business Suite to schedule all posts.
                </p>
              </div>
            )}
          </div>
        </div>
      </div>
    </main>
  );
}
