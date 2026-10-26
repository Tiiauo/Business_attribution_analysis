import {useEffect,useRef,useState} from 'react';
import type {HistoryPage,MessageInfo,ResultInfo,TaskEvent,TaskSnapshot,UserInfo} from './contracts';

export async function request<T>(user:UserInfo,path:string,body?:unknown):Promise<T> {
  const response=await fetch(path,{method:body===undefined?'GET':'POST',cache:'no-store',
    signal:AbortSignal.timeout(15000),
    headers:body===undefined?{}:{'Content-Type':'application/json','X-CSRF-Token':user.csrf_token},
    body:body===undefined?undefined:JSON.stringify(body)});
  const value=await response.json();
  if(!response.ok)throw new Error(`${value.message}（${value.code}；请求 ${value.request_id}）`);
  return value;
}

export function useTaskSession(user:UserInfo,conversationId:string|null,revision:number) {
  const [history,setHistory]=useState<HistoryPage|null>(null),[tasks,setTasks]=useState<Record<string,TaskSnapshot>>({});
  const [result,setResult]=useState<ResultInfo|null>(null),[selectedTask,setSelectedTask]=useState<string|null>(null);
  const [loading,setLoading]=useState(false),[notice,setNotice]=useState('');
  const generation=useRef(0),syncRef=useRef<()=>void>(()=>{}),focus=useRef<string|null>(null),pinned=useRef(false);
  useEffect(()=>{
    const epoch=++generation.current;
    setHistory(null);setTasks({});setResult(null);setSelectedTask(null);focus.current=null;pinned.current=false;
    setLoading(Boolean(conversationId));setNotice('');
    if(!conversationId)return;
    let socket:WebSocket|null=null,retry:ReturnType<typeof setTimeout>|undefined,timer:ReturnType<typeof setTimeout>|undefined;
    let syncing=false,requested=false,buffering=true,round=0;
    const cursors=new Map<string,number>(),pending:TaskEvent[]=[],known=new Map<string,TaskSnapshot>();
    const alive=()=>epoch===generation.current;
    const merge=(items:MessageInfo[],fresh:MessageInfo[])=>[...new Map([...items,...fresh].map(m=>[m.id,m])).values()].sort((a,b)=>a.seq_no-b.seq_no);
    function schedule(){
      if(!alive())return;
      requested=true;
      if(!syncing&&!timer)timer=setTimeout(()=>{timer=undefined;void synchronize();},100);
    }
    function apply(value:TaskEvent){
      const cursor=cursors.get(value.task_id)||0;
      if(value.event_id<=cursor)return;
      if(buffering||value.event_id!==cursor+1){pending.push(value);schedule();return;}
      // Messages absent from the current page are recovered by the same consistent snapshot path.
      const task=known.get(value.task_id);
      if(!task){pending.push(value);schedule();return;}
      let next={...task,last_event_id:value.event_id,messages:task.messages.map(m=>({...m}))};
      if(value.type==='message_delta'){
        const message=next.messages.find(m=>m.id===value.message_id);
        if(!message){pending.push(value);schedule();return;}
        message.content+=value.delta_text;
      } else if(value.type==='task_status')next={...next,task_status:value.task_status,current_step:value.current_step,cancel_requested_at:value.cancel_requested_at,is_cancelling:value.is_cancelling};
      else if(value.type==='error')next.error_message=value.error_message;
      else if(value.type==='done')next.finished_at=value.finished_at;
      else {pending.push(value);schedule();return;}
      known.set(value.task_id,next);cursors.set(value.task_id,value.event_id);
      setTasks(current=>({...current,[value.task_id]:next}));
      setHistory(current=>current?{...current,items:merge(current.items,next.messages)}:current);
    }
    async function synchronize(){
      if(syncing||!alive())return;
      syncing=true;buffering=true;
      try{
        do{
          requested=false;
          const thisRound=++round;
          const page=await request<HistoryPage>(user,'/api/chat/ls/'+conversationId);
          if(!alive()||thisRound!==round)return;
          if(page.conversation.status==='deleted'){
            setHistory(page);setTasks({});setResult(null);setSelectedTask(null);setNotice('');setLoading(false);return;
          }
          if(!pinned.current)focus.current=page.conversation.latest_task_id;
          let selected=focus.current;
          const ids=[...new Set([page.conversation.current_task_id,page.conversation.latest_task_id,selected,...pending.map(value=>value.task_id)].filter((id):id is string=>Boolean(id)))];
          const snapshots=await Promise.all(ids.map(id=>request<TaskSnapshot>(user,'/api/tasks/'+id)));
          let selectedSnapshot=snapshots.find(task=>task.task_id===selected);
          if(!pinned.current&&selectedSnapshot&&['queued','running'].includes(selectedSnapshot.task_status)){
            const previous=[...page.items].reverse().find(message=>message.role==='assistant'&&message.message_status==='complete'&&message.task_id!==selected);
            if(previous){
              const saved=snapshots.find(task=>task.task_id===previous.task_id)||await request<TaskSnapshot>(user,'/api/tasks/'+previous.task_id);
              if(saved.task_status==='success'){selected=previous.task_id;focus.current=selected;selectedSnapshot=saved;if(!snapshots.includes(saved))snapshots.push(saved);}
            }
          }
          const output=selectedSnapshot?.task_status==='success'?await request<ResultInfo>(user,'/api/results/'+selected):null;
          if(!alive()||thisRound!==round)return;
          if(snapshots.some(task=>task.last_event_id<(cursors.get(task.task_id)||0))){schedule();break;}
          let items=page.items;
          for(const task of snapshots){
            cursors.set(task.task_id,task.last_event_id);known.set(task.task_id,task);
            items=merge(items,task.messages);
          }
          setHistory(current=>({...page,items:merge(current?.items||[],items),next_cursor:current?current.next_cursor:page.next_cursor}));
          setTasks(Object.fromEntries(known));
          if(focus.current===selected){setSelectedTask(selected);setResult(output);}
          setNotice('');setLoading(false);
          buffering=false;
          const buffered=pending.splice(0).sort((a,b)=>a.task_id.localeCompare(b.task_id)||a.event_id-b.event_id);
          for(const value of buffered)apply(value);
          buffering=true;
          // Gaps may refer to a task created after the metadata read; yield before the next transaction.
          if(requested)break;
        }while(requested&&alive());
      }catch(reason){if(alive()){setNotice((reason as Error).message);setLoading(false);}}
      finally{buffering=false;syncing=false;if(requested&&alive())schedule();}
    }
    async function connect(){
      try{
        const token=await request<{websocket_token:string}>(user,'/api/chat/ws-token',{conversation_id:conversationId});
        if(!alive())return;
        socket=new WebSocket(`${location.protocol==='https:'?'wss:':'ws:'}//${location.host}/api/chat/ws/chat?conversation_id=${conversationId}&websocket_token=${encodeURIComponent(token.websocket_token)}`);
        socket.onmessage=message=>{
          if(!alive())return;
          try{
            const value=JSON.parse(message.data);
            if(value.type==='subscription_ready'){schedule();return;}
            if(value.conversation_id===conversationId&&typeof value.event_id==='number')apply(value as TaskEvent);
          }catch{schedule();}
        };
        socket.onclose=()=>{if(alive()){setNotice('实时连接已断开，正在重连；后台任务继续执行。');retry=setTimeout(()=>void connect(),1500);}};
      }catch(reason){if(alive()){setNotice((reason as Error).message);retry=setTimeout(()=>void connect(),3000);}}
    }
    syncRef.current=schedule;
    void connect();
    const interval=setInterval(()=>{if(document.visibilityState==='visible')schedule();},5000);
    const visible=()=>{if(document.visibilityState==='visible')schedule();};document.addEventListener('visibilitychange',visible);
    return()=>{generation.current++;syncRef.current=()=>{};clearInterval(interval);clearTimeout(timer);clearTimeout(retry);socket?.close();document.removeEventListener('visibilitychange',visible);};
  },[user.id,user.csrf_token,conversationId,revision]);
  return {history,setHistory,tasks,result,selectedTask,loading,notice,synchronize:()=>syncRef.current(),
    selectTask:(id:string)=>{pinned.current=true;focus.current=id;setSelectedTask(id);setResult(null);syncRef.current();}};
}
