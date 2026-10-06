#!/usr/bin/env python3
"""Sleepy-Chat: WebRTC voice and video chat with Supabase Realtime signaling."""
import os, sys

INDEX = "index.html"
MARK  = "SLEEPY_VOICE_VIDEO_V1"

PATCH = r"""<!-- SLEEPY_VOICE_VIDEO_V1 -->
<style>
.voice-dock{
  position:fixed;bottom:80px;right:20px;z-index:400;
  background:linear-gradient(135deg, rgba(0,255,224,.08), rgba(180,74,255,.08)), #12121a;
  border:1px solid rgba(0,255,224,.35);border-radius:14px;
  padding:12px 16px;display:flex;align-items:center;gap:12px;
  box-shadow:0 20px 50px rgba(0,0,0,.6);animation:fadeUp .3s ease;
}
.voice-dock .live{display:flex;align-items:center;gap:6px;font-size:12px;font-weight:700;color:var(--green);text-transform:uppercase;letter-spacing:.08em}
.voice-dock .live i{width:8px;height:8px;border-radius:50%;background:var(--green);animation:pulse 2s infinite}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.4}}
.voice-dock .names{font-size:12px;color:var(--text2);max-width:150px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.voice-dock button{
  width:36px;height:36px;border-radius:9px;display:flex;align-items:center;justify-content:center;
  background:var(--surface2);border:1px solid var(--border);color:var(--text2);transition:all .15s;
}
.voice-dock button:hover{border-color:var(--border2);color:var(--text)}
.voice-dock button.danger{background:rgba(255,45,111,.15);border-color:rgba(255,45,111,.35);color:#ff7aa2}
.voice-dock button.on{background:rgba(0,255,224,.15);border-color:rgba(0,255,224,.4);color:var(--cyan)}
.voice-dock button svg{width:16px;height:16px}
.video-grid{position:fixed;inset:0;z-index:300;background:rgba(4,4,8,.92);backdrop-filter:blur(6px);display:flex;align-items:center;justify-content:center;padding:20px}
.video-grid .tiles{display:flex;flex-wrap:wrap;gap:10px;justify-content:center;max-width:1200px}
.video-grid .tile{width:280px;height:210px;background:#000;border-radius:12px;overflow:hidden;position:relative;border:1px solid var(--border)}
.video-grid .tile video{width:100%;height:100%;object-fit:cover}
.video-grid .tile .label{position:absolute;bottom:6px;left:8px;font-size:11px;color:var(--text2);background:rgba(0,0,0,.6);padding:2px 6px;border-radius:4px}
.video-grid .tile .muted-icon{position:absolute;top:6px;right:8px;color:var(--rose);font-size:12px}
</style>
<script>
(function(){
  "use strict";
  if (window.__sleepyVoiceVideo) return;
  window.__sleepyVoiceVideo = true;
  function log(){ try{console.log.apply(console,["[VC]"].concat([].slice.call(arguments)));}catch(e){} }

  // ---- State ----
  window.callState = {
    channelId: null,
    localStream: null,
    peers: {},              // peerId -> RTCPeerConnection
    audioElements: {},      // peerId -> HTMLAudioElement
    videoElements: {},      // peerId -> HTMLVideoElement
    muted: false,
    deafened: false,
    videoOn: false,
    signalingChannel: null,
    presenceChannel: null,
    iceServers: null,
    videoGridVisible: false,
  };

  // ---- 1. Get media ----
  async function getMedia(video){
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: true,
        video: video ? { width: 640, height: 480 } : false
      });
      window.callState.localStream = stream;
      window.callState.videoOn = video;
      log("media acquired, video:", video);
      return stream;
    } catch(e){
      log("media error:", e.message);
      window.toast("Device access blocked", "Please allow mic and camera access.", "err");
      throw e;
    }
  }

  // ---- 2. Get TURN credentials ----
  async function getIceServers(){
    if (window.callState.iceServers) return window.callState.iceServers;
    try {
      const session = await sb.auth.getSession();
      const token = session?.data?.session?.access_token;
      const res = await fetch(SUPABASE_URL + "/functions/v1/turn-credentials", {
        method: "POST",
        headers: { "Authorization": "Bearer " + token, "apikey": SUPABASE_ANON_KEY },
      });
      if (!res.ok) throw new Error("TURN fetch failed: " + res.status);
      const data = await res.json();
      window.callState.iceServers = data.iceServers;
      return data.iceServers;
    } catch(e){
      log("fallback to STUN only:", e.message);
      const fallback = [{ urls: "stun:stun.l.google.com:19302" }];
      window.callState.iceServers = fallback;
      return fallback;
    }
  }

  // ---- 3. Join Voice/Video Channel ----
  window.joinVoice = async function(channelId, withVideo){
    if (window.callState.channelId === channelId) return;
    if (window.callState.channelId) await window.leaveVoice();

    log("joining call:", channelId, "video:", withVideo);
    window.callState.channelId = channelId;

    await getMedia(withVideo);
    const iceServers = await getIceServers();

    // Save to DB
    try {
      await sb.from('voice_participants').upsert({
        channel_id: channelId,
        user_id: window.ME.id,
        is_muted: false,
        is_deafened: false,
        is_video_on: withVideo,
      });
    } catch(e){ log("participant insert failed:", e.message); }

    // Signaling channel
    const sigChannelName = 'call-signal-' + channelId;
    window.callState.signalingChannel = sb.channel(sigChannelName, {
      config: { broadcast: { self: false } },
    });

    window.callState.signalingChannel
      .on('broadcast', { event: 'offer' }, async (payload) => {
        log("received offer from:", payload.payload.from);
        await handleOffer(payload.payload.from, payload.payload.sdp, iceServers);
      })
      .on('broadcast', { event: 'answer' }, async (payload) => {
        log("received answer from:", payload.payload.from);
        const pc = window.callState.peers[payload.payload.from];
        if (pc) await pc.setRemoteDescription(new RTCSessionDescription(payload.payload.sdp));
      })
      .on('broadcast', { event: 'ice-candidate' }, async (payload) => {
        const pc = window.callState.peers[payload.payload.from];
        if (pc && payload.payload.candidate) {
          try { await pc.addIceCandidate(new RTCIceCandidate(payload.payload.candidate)); } catch(e){ log("ICE add failed:", e.message); }
        }
      })
      .on('broadcast', { event: 'leave-call' }, (payload) => {
        log("peer left:", payload.payload.from);
        closePeer(payload.payload.from);
      })
      .on('broadcast', { event: 'join-call' }, (payload) => {
        const newPeerId = payload.payload.from;
        if (newPeerId === window.ME.id) return;
        if (!window.callState.peers[newPeerId]) {
          log("new peer joined, creating offer:", newPeerId);
          createOffer(newPeerId, iceServers);
        }
      })
      .subscribe(async (status) => {
        if (status === 'SUBSCRIBED') {
          log("signaling channel ready");
          window.callState.signalingChannel.send({
            type: 'broadcast', event: 'join-call',
            payload: { from: window.ME.id, username: window.ME.username },
          });
        }
      });

    // Presence channel
    window.callState.presenceChannel = sb.channel('call-presence-' + channelId, {
      config: { presence: { key: window.ME.id } },
    });
    window.callState.presenceChannel
      .on('presence', { event: 'sync' }, () => {
        const state = window.callState.presenceChannel.presenceState();
        for (const peerId in state) {
          if (peerId !== window.ME.id && !window.callState.peers[peerId]) {
            createOffer(peerId, iceServers);
          }
        }
      })
      .on('presence', { event: 'leave' }, (o) => { closePeer(o.key); })
      .subscribe(async (status) => {
        if (status === 'SUBSCRIBED') {
          await window.callState.presenceChannel.track({
            user_id: window.ME.id,
            username: window.ME.username,
            at: new Date().toISOString(),
          });
        }
      });

    renderVoiceDock();
    if (withVideo) renderVideoGrid();
    window.toast("Call joined", "You are now in the call.", "ok");
  };

  // ---- 4. Create WebRTC offer ----
  async function createOffer(peerId, iceServers){
    try {
      const pc = new RTCPeerConnection({ iceServers });
      window.callState.peers[peerId] = pc;

      window.callState.localStream.getTracks().forEach(track => {
        pc.addTrack(track, window.callState.localStream);
      });

      pc.ontrack = (event) => {
        log("got remote track from:", peerId);
        const stream = event.streams[0];
        if (stream.getAudioTracks().length) {
          const audio = document.createElement('audio');
          audio.srcObject = stream;
          audio.autoplay = true;
          audio.id = 'call-audio-' + peerId;
          document.body.appendChild(audio);
          window.callState.audioElements[peerId] = audio;
        }
        if (stream.getVideoTracks().length) {
          const video = document.createElement('video');
          video.srcObject = stream;
          video.autoplay = true;
          video.playsInline = true;
          video.id = 'call-video-' + peerId;
          window.callState.videoElements[peerId] = video;
          renderVideoGrid();
        }
        applyAudioState();
      };

      pc.onicecandidate = (event) => {
        if (event.candidate) {
          window.callState.signalingChannel.send({
            type: 'broadcast', event: 'ice-candidate',
            payload: { from: window.ME.id, candidate: event.candidate },
          });
        }
      };

      pc.onconnectionstatechange = () => {
        log("peer", peerId, "state:", pc.connectionState);
        if (pc.connectionState === 'failed' || pc.connectionState === 'disconnected') {
          closePeer(peerId);
        }
      };

      const offer = await pc.createOffer();
      await pc.setLocalDescription(offer);

      window.callState.signalingChannel.send({
        type: 'broadcast', event: 'offer',
        payload: { from: window.ME.id, sdp: pc.localDescription },
      });

      log("offer sent to:", peerId);
    } catch(e){ log("createOffer failed:", e.message); }
  }

  // ---- 5. Handle incoming offer ----
  async function handleOffer(peerId, sdp, iceServers){
    try {
      const pc = new RTCPeerConnection({ iceServers });
      window.callState.peers[peerId] = pc;

      window.callState.localStream.getTracks().forEach(track => {
        pc.addTrack(track, window.callState.localStream);
      });

      pc.ontrack = (event) => {
        const stream = event.streams[0];
        if (stream.getAudioTracks().length) {
          const audio = document.createElement('audio');
          audio.srcObject = stream;
          audio.autoplay = true;
          audio.id = 'call-audio-' + peerId;
          document.body.appendChild(audio);
          window.callState.audioElements[peerId] = audio;
        }
        if (stream.getVideoTracks().length) {
          const video = document.createElement('video');
          video.srcObject = stream;
          video.autoplay = true;
          video.playsInline = true;
          video.id = 'call-video-' + peerId;
          window.callState.videoElements[peerId] = video;
          renderVideoGrid();
        }
        applyAudioState();
      };

      pc.onicecandidate = (event) => {
        if (event.candidate) {
          window.callState.signalingChannel.send({
            type: 'broadcast', event: 'ice-candidate',
            payload: { from: window.ME.id, candidate: event.candidate },
          });
        }
      };

      await pc.setRemoteDescription(new RTCSessionDescription(sdp));
      const answer = await pc.createAnswer();
      await pc.setLocalDescription(answer);

      window.callState.signalingChannel.send({
        type: 'broadcast', event: 'answer',
        payload: { from: window.ME.id, sdp: pc.localDescription },
      });
    } catch(e){ log("handleOffer failed:", e.message); }
  }

  // ---- 6. Close a peer ----
  function closePeer(peerId){
    const pc = window.callState.peers[peerId];
    if (pc) { try { pc.close(); } catch(e){} delete window.callState.peers[peerId]; }
    const audio = window.callState.audioElements[peerId];
    if (audio) { audio.remove(); delete window.callState.audioElements[peerId]; }
    const video = window.callState.videoElements[peerId];
    if (video) { video.remove(); delete window.callState.videoElements[peerId]; }
    renderVideoGrid();
    log("closed peer:", peerId);
  }

  // ---- 7. Leave Call ----
  window.leaveVoice = async function(){
    log("leaving call");
    if (window.callState.signalingChannel) {
      try {
        window.callState.signalingChannel.send({
          type: 'broadcast', event: 'leave-call',
          payload: { from: window.ME.id },
        });
      } catch(e){}
      sb.removeChannel(window.callState.signalingChannel);
      window.callState.signalingChannel = null;
    }
    if (window.callState.presenceChannel) {
      try { await window.callState.presenceChannel.untrack(); } catch(e){}
      sb.removeChannel(window.callState.presenceChannel);
      window.callState.presenceChannel = null;
    }

    if (window.callState.channelId) {
      try {
        await sb.from('voice_participants')
          .delete()
          .eq('channel_id', window.callState.channelId)
          .eq('user_id', window.ME.id);
      } catch(e){}
    }

    for (const peerId in window.callState.peers) closePeer(peerId);
    window.callState.peers = {};
    window.callState.audioElements = {};
    window.callState.videoElements = {};

    if (window.callState.localStream) {
      window.callState.localStream.getTracks().forEach(t => t.stop());
      window.callState.localStream = null;
    }

    window.callState.channelId = null;
    window.callState.videoOn = false;
    window.callState.videoGridVisible = false;
    const grid = document.getElementById('video-grid');
    if (grid) grid.remove();
    renderVoiceDock();
    window.toast("Call left", "You left the call.", "ok");
  };

  // ---- 8. Mute / Deafen / Video toggle ----
  window.toggleMute = function(){
    const s = window.callState;
    if (!s.localStream) return;
    s.muted = !s.muted;
    s.localStream.getAudioTracks().forEach(t => { t.enabled = !s.muted; });
    if (s.channelId) {
      sb.from('voice_participants').update({ is_muted: s.muted })
        .eq('channel_id', s.channelId).eq('user_id', window.ME.id).then(()=>{});
    }
    renderVoiceDock();
    log("muted:", s.muted);
  };

  window.toggleDeafen = function(){
    const s = window.callState;
    s.deafened = !s.deafened;
    applyAudioState();
    renderVoiceDock();
    log("deafened:", s.deafened);
  };

  window.toggleVideo = function(){
    const s = window.callState;
    if (!s.localStream) return;
    s.videoOn = !s.videoOn;
    s.localStream.getVideoTracks().forEach(t => { t.enabled = s.videoOn; });
    if (s.channelId) {
      sb.from('voice_participants').update({ is_video_on: s.videoOn })
        .eq('channel_id', s.channelId).eq('user_id', window.ME.id).then(()=>{});
    }
    if (s.videoOn) renderVideoGrid();
    renderVoiceDock();
    log("video on:", s.videoOn);
  };

  function applyAudioState(){
    const deafened = window.callState.deafened;
    for (const id in window.callState.audioElements) {
      window.callState.audioElements[id].muted = deafened;
    }
  }

  // ---- 9. Voice Dock UI ----
  function renderVoiceDock(){
    const existing = document.getElementById('voice-dock');
    if (existing) existing.remove();
    if (!window.callState.channelId) return;

    const ch = window.CHANNELS ? window.CHANNELS.find(c => c.id === window.callState.channelId) : null;
    const chName = ch ? ch.name : 'call';
    const peers = Object.keys(window.callState.peers);

    const dock = document.createElement('div');
    dock.id = 'voice-dock';
    dock.className = 'voice-dock';
    dock.innerHTML =
      '<div class="live"><i></i>Call</div>' +
      '<div class="names">#' + chName + ' · ' + (peers.length + 1) + ' connected</div>' +
      '<button id="call-mute" class="' + (window.callState.muted ? 'on' : '') + '" title="Mute">' +
        '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="2" width="6" height="11" rx="3"/><path d="M5 10v1a7 7 0 0 0 14 0v-1M12 18v4M8 22h8"/></svg>' +
      '</button>' +
      '<button id="call-deafen" class="' + (window.callState.deafened ? 'on' : '') + '" title="Deafen">' +
        '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 18v-6a9 9 0 0 1 18 0v6"/><path d="M21 19a2 2 0 0 1-2 2h-1a2 2 0 0 1-2-2v-3a2 2 0 0 1 2-2h3zM3 19a2 2 0 0 0 2 2h1a2 2 0 0 0 2-2v-3a2 2 0 0 0-2-2H3z"/></svg>' +
      '</button>' +
      '<button id="call-video" class="' + (window.callState.videoOn ? 'on' : '') + '" title="Camera">' +
        '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="6" width="14" height="12" rx="2"/><path d="m22 8-6 4 6 4V8z"/></svg>' +
      '</button>' +
      '<button id="call-leave" class="danger" title="Disconnect">' +
        '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><path d="M18 6 6 18M6 6l12 12"/></svg>' +
      '</button>';

    document.body.appendChild(dock);

    document.getElementById('call-mute').onclick = window.toggleMute;
    document.getElementById('call-deafen').onclick = window.toggleDeafen;
    document.getElementById('call-video').onclick = window.toggleVideo;
    document.getElementById('call-leave').onclick = window.leaveVoice;
  }

  // ---- 10. Video Grid ----
  function renderVideoGrid(){
    let grid = document.getElementById('video-grid');
    if (!window.callState.videoOn && Object.keys(window.callState.videoElements).length === 0) {
      if (grid) grid.remove();
      return;
    }
    if (!grid) {
      grid = document.createElement('div');
      grid.id = 'video-grid';
      grid.className = 'video-grid';
      grid.onclick = (e) => { if (e.target === grid) { grid.style.display = grid.style.display === 'none' ? 'flex' : 'none'; } };
      document.body.appendChild(grid);
    }
    grid.style.display = 'flex';

    let html = '<div class="tiles">';
    // Local video
    if (window.callState.localStream && window.callState.videoOn) {
      html += '<div class="tile"><video id="local-video" autoplay playsinline muted></video><div class="label">You</div></div>';
    }
    // Remote videos
    for (const peerId in window.callState.videoElements) {
      const p = window.PROFILES[peerId] || { display_name: 'User' };
      html += '<div class="tile"><video id="remote-video-' + peerId + '" autoplay playsinline></video><div class="label">' + window.esc(p.display_name || p.username) + '</div></div>';
    }
    html += '</div>';
    grid.innerHTML = html;

    const localVideo = document.getElementById('local-video');
    if (localVideo && window.callState.localStream) localVideo.srcObject = window.callState.localStream;
    for (const peerId in window.callState.videoElements) {
      const el = document.getElementById('remote-video-' + peerId);
      if (el) el.srcObject = window.callState.videoElements[peerId].srcObject;
    }
  }

  // ---- 11. Patch channel click to offer Join ----
  const origGoChannel = window.goChannel;
  window.goChannel = async function(channelId){
    const ch = window.CHANNELS ? window.CHANNELS.find(c => c.id === channelId) : null;
    if (ch && (ch.type === 'voice' || ch.type === 'video')) {
      const body = document.getElementById('chat-body');
      if (body) {
        const { data: participants } = await sb.from('voice_participants').select('user_id').eq('channel_id', channelId);
        const pIds = (participants || []).map(r => r.user_id);
        await window.fetchProfiles(pIds);

        const pList = pIds.map(uid =>
          '<div style="display:flex;flex-direction:column;align-items:center;gap:6px;width:76px">' +
            window.avatarHTML(uid, 48) +
            '<div style="font-size:11px;color:var(--text2)">' + window.esc(window.userLabel(uid)) + '</div>' +
          '</div>'
        ).join('');

        const isInChannel = window.callState.channelId === channelId;

        body.innerHTML =
          '<div class="empty" style="margin-top:10vh">' +
            '<div class="big" style="background:linear-gradient(135deg,rgba(0,255,224,.14),rgba(180,74,255,.14));border:1px solid rgba(0,255,224,.22);width:72px;height:72px;border-radius:20px;display:flex;align-items:center;justify-content:center;margin:0 auto 14px">' +
              '<svg viewBox="0 0 24 24" fill="none" stroke="#00ffe0" stroke-width="1.8" style="width:34px;height:34px"><path d="M11 5 6 9H3v6h3l5 4V5z"/><path d="M15.5 8.5a5 5 0 0 1 0 7"/><path d="M18.5 5.5a9 9 0 0 1 0 13"/></svg>' +
            '</div>' +
            '<h2 style="font-family:Space Grotesk;color:var(--text)">' + window.esc(ch.name) + '</h2>' +
            '<p style="margin-top:6px">' + (ch.topic ? window.esc(ch.topic) : 'Voice & Video channel') + '</p>' +
            (pList ? '<div style="display:flex;flex-wrap:wrap;gap:14px;justify-content:center;margin-top:20px">' + pList + '</div>' : '<p style="margin-top:14px;color:var(--text3)">Nobody is here yet.</p>') +
            '<div style="margin-top:22px;display:flex;gap:10px;justify-content:center">' +
              (isInChannel
                ? '<button class="btn danger" onclick="window.leaveVoice()">Leave Call</button>'
                : '<button class="btn primary" onclick="window.joinVoice(\'' + channelId + '\', false)">Join Voice</button>' +
                  '<button class="btn primary" style="background:linear-gradient(135deg,#b44aff,#7c2fd6);color:#fff" onclick="window.joinVoice(\'' + channelId + '\', true)">Join Video</button>') +
            '</div>' +
          '</div>';
      }
      window.renderChatHead();
      return;
    }
    return origGoChannel(channelId);
  };

  log("voice & video chat ready");
})();
</script>
"""

def main():
    here = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(here, INDEX)
    if not os.path.exists(path):
        print("index.html not found next to this script."); sys.exit(1)
    with open(path, "rb") as f: raw = f.read()
    try: s = raw.decode("utf-8")
    except UnicodeDecodeError: s = raw.decode("utf-8", errors="replace")
    s = s.replace("\r\n", "\n").replace("\r", "\n")
    if MARK in s:
        print("Already patched."); return
    if "</body>" not in s:
        print("ERROR: </body> missing."); sys.exit(1)
    s = s.replace("</body>", PATCH + "\n</body>", 1)
    with open(path, "wb") as f: f.write(s.encode("utf-8"))
    print("Voice & Video chat patch installed.")
    print("Hard-refresh index.html (Ctrl+F5).")

if __name__ == "__main__":
    main()
