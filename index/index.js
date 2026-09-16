const bgAudio = wx.getBackgroundAudioManager();
const API_BASE = "https://xxxx.com"; 

Page({
  data: {
    grades: [],
    gradeIndex: 0,
    fileObjList: [], 
    text: "", 
    shuffleBool: false,
    pauseSeconds: 1.5,
    speed: 0.85,
    pitch: -15,
    
    voiceArray: [
      { id: 'zh-CN-XiaoxiaoNeural', name: '标准女声 (晓晓)' },
      { id: 'zh-CN-YunxiNeural', name: '故事男声 (云希)' },
      { id: 'zh-CN-XiaoyiNeural', name: '温柔女声 (晓伊)' },
      { id: 'zh-CN-YunjianNeural', name: '严肃男声 (云健)' },
      { id: 'zh-CN-YunyangNeural', name: '新闻男声 (云扬)' }
    ],
    voiceIndex: 0,
    
    // 🚨 完美恢复：页面所需的 UI 状态变量
    isLoading: false, 
    audioUrl: "", 
    isPlaying: false
  },

  onLoad() {
    this.fetchGrades();

    // 保持屏幕常亮
    wx.setKeepScreenOn({
      keepScreenOn: true,
      success: () => console.log('屏幕常亮已开启')
    });

    // 监听后台播放器状态，同步更新给你的前端 UI
    bgAudio.onPlay(() => { this.setData({ isPlaying: true }); });
    bgAudio.onPause(() => { this.setData({ isPlaying: false }); });
    bgAudio.onEnded(() => { this.setData({ isPlaying: false }); });
    bgAudio.onError((res) => {
      console.error('后台音频报错:', res);
      this.setData({ isPlaying: false });
      wx.showToast({ title: '播放断开，请重试', icon: 'none' });
    });
  },

  fetchGrades() {
    wx.request({
      url: `${API_BASE}/api/grades`,
      method: 'GET',
      success: (res) => {
        if (res.data.grades && res.data.grades.length > 0) {
          this.setData({ grades: res.data.grades, gradeIndex: 0 });
          this.fetchFiles(res.data.grades[0]);
        }
      }
    });
  },

  onGradeChange(e) {
    const idx = e.detail.value;
    this.setData({ gradeIndex: idx });
    this.fetchFiles(this.data.grades[idx]);
  },

  fetchFiles(grade) {
    wx.request({
      url: `${API_BASE}/api/files/${grade}`,
      method: 'GET',
      success: (res) => {
        const files = res.data.files || [];
        const fileObjList = files.map((f, index) => ({ name: f, selected: index === 0 }));
        this.setData({ fileObjList: fileObjList });
        
        const selectedFiles = fileObjList.filter(f => f.selected).map(f => f.name);
        if (selectedFiles.length > 0) {
          this.fetchContent(grade, selectedFiles);
        } else {
          this.setData({ text: "" });
        }
      }
    });
  },

  toggleFileSelection(e) {
    const idx = e.currentTarget.dataset.index;
    let currentList = this.data.fileObjList;
    currentList[idx].selected = !currentList[idx].selected;
    this.setData({ fileObjList: currentList });

    const selectedFiles = currentList.filter(f => f.selected).map(f => f.name);
    if (selectedFiles.length > 0) {
      this.fetchContent(this.data.grades[this.data.gradeIndex], selectedFiles);
    } else {
      this.setData({ text: "" }); 
    }
  },

  fetchContent(grade, filenamesArray) {
    wx.request({
      url: `${API_BASE}/api/content`,
      method: 'POST',
      data: { grade: grade, files: filenamesArray }, 
      success: (res) => {
        this.setData({ text: res.data.text || "" });
      }
    });
  },

  onInputText(e) { this.setData({ text: e.detail.value }); },
  onShuffleChange(e) { this.setData({ shuffleBool: e.detail.value }); },
  onVoiceChange(e) { this.setData({ voiceIndex: e.detail.value }); },
  onPauseChange(e) { this.setData({ pauseSeconds: e.detail.value }); },
  onSpeedChange(e) { this.setData({ speed: e.detail.value }); },

  generateAudio() {
    if (!this.data.text.trim()) return wx.showToast({ title: '没有可听写的词语', icon: 'none' });
    
    // 🚨 恢复 UI 加载状态
    this.setData({ isLoading: true, audioUrl: "", isPlaying: false });
    
    const currentGrade = this.data.grades[this.data.gradeIndex] || '听写任务';
    const selectedFiles = this.data.fileObjList.filter(f => f.selected).map(f => f.name);
    let lessonTitle = selectedFiles.length > 0 ? selectedFiles.join('、') : '自定义内容';
    const finalAudioTitle = `${currentGrade}：${lessonTitle}`;

    const encodedText = encodeURIComponent(this.data.text);
    const voiceId = this.data.voiceArray[this.data.voiceIndex].id;
    const streamUrl = `${API_BASE}/api/stream?text=${encodedText}&voice=${voiceId}&speed=${this.data.speed}&pause_seconds=${this.data.pauseSeconds}&pitch=${this.data.pitch}&shuffle_bool=${this.data.shuffleBool}`;

    // 🚨 核心修复：重新给 audioUrl 赋值，你的 WXML 界面监听到它有值了，就会把播放框架弹出来！
    this.setData({ 
        audioUrl: streamUrl,
        isLoading: false 
    });

    // 同时直接喂给底层背景播放器，实现无缝衔接
    bgAudio.title = finalAudioTitle;
    bgAudio.epname = '专属听写助手';
    bgAudio.singer = this.data.voiceArray[this.data.voiceIndex].name; 
    bgAudio.src = streamUrl; 
  },

  playAudio() {
    if (this.data.isPlaying) {
      bgAudio.pause();
    } else {
      if (this.data.audioUrl) {
        // 防止后台播放器闲置清空，若空了就重新赋值
        if (!bgAudio.src) {
          bgAudio.title = '专属听写助手';
          bgAudio.src = this.data.audioUrl;
        } else {
          bgAudio.play();
        }
      } else {
        wx.showToast({ title: '请先生成音频', icon: 'none' });
      }
    }
  },

  stopAudio() {
    bgAudio.stop();
    this.setData({ isPlaying: false });
  }
});
