import gradio as gr
import asyncio
import edge_tts
import os
import uuid
import re
import random
from pydub import AudioSegment

# ================= 1. 动态物理文件扫描与创建引擎 =================
BASE_DIR = "data"

def natural_sort_key(s):
    return [int(text) if text.isdigit() else text.lower() for text in re.split(r'(\d+)', s)]

def get_dynamic_grades():
    if not os.path.exists(BASE_DIR):
        os.makedirs(BASE_DIR)
    grades = [d for d in os.listdir(BASE_DIR) if os.path.isdir(os.path.join(BASE_DIR, d))]
    grades.sort(key=natural_sort_key)
    return grades if grades else ["请先创建年级"]

def get_dynamic_files(grade):
    grade_path = os.path.join(BASE_DIR, grade)
    if not os.path.exists(grade_path) or not os.path.isdir(grade_path):
        return []
    files = []
    for f in os.listdir(grade_path):
        name, ext = os.path.splitext(f)
        if ext.lower() == ".txt":
            files.append(name)
    files.sort(key=natural_sort_key)
    return files

def load_multiple_files_content(grade, file_names):
    if not file_names or "请先" in grade:
        return ""
    if isinstance(file_names, str):
        file_names = [file_names]
    combined_words = []
    for fn in file_names:
        file_path = os.path.join(BASE_DIR, grade, f"{fn}.txt")
        if os.path.exists(file_path):
            with open(file_path, "r", encoding="utf-8") as f:
                content = f.read().strip()
                if content:
                    combined_words.append(content)
    return "、".join(combined_words)

def on_grade_change(grade):
    files = get_dynamic_files(grade)
    if files:
        default_file = [files[0]]
        text = load_multiple_files_content(grade, default_file)
        return gr.update(choices=files, value=default_file), text
    return gr.update(choices=[], value=[]), ""

def on_select_all_click(grade):
    files = get_dynamic_files(grade)
    if files:
        text = load_multiple_files_content(grade, files)
        return gr.update(value=files), text
    return gr.update(value=[]), ""

def create_new_grade_folder(new_grade_name):
    name = new_grade_name.strip()
    if not name:
        raise gr.Error("年级名称不能为空！")
    path = os.path.join(BASE_DIR, name)
    if not os.path.exists(path):
        os.makedirs(path)
    all_grades = get_dynamic_grades()
    return gr.update(choices=all_grades, value=name), ""

def create_new_lesson_file(grade, new_lesson_name):
    if "请先" in grade or not grade:
        raise gr.Error("请先选择或创建一个有效的年级！")
    name = new_lesson_name.strip()
    if not name:
        raise gr.Error("课时编号/名称不能为空！")
    file_path = os.path.join(BASE_DIR, grade, f"{name}.txt")
    if not os.path.exists(file_path):
        with open(file_path, "w", encoding="utf-8") as f:
            f.write("")
    files = get_dynamic_files(grade)
    return gr.update(choices=files, value=[name]), "", "创建成功！可在上方输入框内录入新词。"

# ================= 2. 核心卡秒音频拼接逻辑 =================
async def generate_single_word(word, voice, speed_rate, pitch_rate):
    temp_filename = f"text_{uuid.uuid4().hex[:8]}.mp3"
    communicate = edge_tts.Communicate(text=word, voice=voice, rate=speed_rate, pitch=pitch_rate)
    await communicate.save(temp_filename)
    return temp_filename

def process_dictation(text, voice, speed, pause_seconds, pitch, shuffle_bool):
    raw_words = re.split(r'[,，\s、\n\r]+', text)
    words = [w.strip() for w in raw_words if w.strip()]
    if not words:
        raise gr.Error("当前输入框内是空的，请确保框内有需要听写的词语！")
    if shuffle_bool:
        random.shuffle(words)

    speed_rate = f"{int((speed - 1.0) * 100)}%" if speed != 1.0 else "+0%"
    pitch_rate = f"{int(pitch):+d}Hz"
    final_audio = AudioSegment.empty()
    pause_ms = int(pause_seconds * 1000)
    silence_gap = AudioSegment.silent(duration=pause_ms)
    word_gap = AudioSegment.silent(duration=pause_ms + 500)

    try:
        for i, word in enumerate(words):
            temp_mp3 = asyncio.run(generate_single_word(word, voice, speed_rate, pitch_rate))
            if not os.path.exists(temp_mp3) or os.path.getsize(temp_mp3) == 0:
                continue
            word_segment = AudioSegment.from_mp3(temp_mp3)
            word_block = word_segment + silence_gap + word_segment + silence_gap + word_segment
            final_audio += word_block
            if os.path.exists(temp_mp3):
                os.remove(temp_mp3)
            if i < len(words) - 1:
                final_audio += word_gap
        if len(final_audio) == 0:
            return None
        unique_output = f"dictation_perfect_{uuid.uuid4().hex[:8]}.mp3"
        final_audio.export(unique_output, format="mp3")
        return unique_output
    except Exception as e:
        print(f"音频合并故障: {e}")
        return None

# ================= 3. 高透干净版 iOS 样式与播放器“去进度条”专属 CSS =================
ios_glass_css = """
body, .gradio-container {
    background-image: url('https://images.unsplash.com/photo-1504309092620-4d0ec726efa4?auto=format&fit=crop&w=1920&q=80') !important;
    background-size: cover !important;
    background-position: center !important;
    background-attachment: fixed !important;
    font-family: -apple-system, BlinkMacSystemFont, "SF Pro Text", Helvetica, Arial, sans-serif !important;
}

#main-title {
    text-align: center !important;
    margin-bottom: 22px !important;
}

/*  ✨ 极简美美化：苹果官网质感纯白大标题 ✨  */
#main-title h1 {
    font-family: -apple-system, BlinkMacSystemFont, "SF Pro Display", "Helvetica Neue", "PingFang SC", sans-serif !important; 
    
    /* 🔴 彻底重塑：去除任何彩色和渐变，换成高雅纯白色 */
    color: #ffffff !important;
    -webkit-text-fill-color: #ffffff !important;
    background: none !important;
    
    font-size: 3.4rem !important; 
    font-weight: 800 !important;
    letter-spacing: -0.02em !important; 
    
    /* 视网膜级轻微雾面阴影，保障在亮色泳池图上清晰、立体、不刺眼 */
    text-shadow: 0px 4px 15px rgba(0, 0, 0, 0.15) !important; 
    margin-bottom: 4px !important;
}

#main-title p {
    color: #edf2f7 !important; /* 标语语同步换成温柔的浅亮色，彻底告别黑色 */
    text-shadow: 0px 2px 8px rgba(0, 0, 0, 0.2) !important;
    font-family: "PingFang SC", sans-serif !important; 
    font-size: 1.05rem !important;
    font-weight: 600 !important;
    letter-spacing: 0.04em !important;
    margin-bottom: 15px !important;
}

/* 💥 降维打击：强行抹除 Hugging Face 顶部的官方悬浮点赞工具栏 */
header, .hf-header, div[class*="space-header"], div[id*="header"], div[class*="Header"] {
    display: none !important;
    visibility: hidden !important;
    height: 0 !important;
    padding: 0 !important;
    margin: 0 !important;
    opacity: 0 !important;
    pointer-events: none !important;
}
    

/*  💥 强迫症福音：彻底干掉 Gradio 官方底部页脚声明  */
footer, .footer, .gradio-container footer, div[class*="footer"] {
    display: none !important;
    visibility: hidden !important;
    height: 0 !important;
    padding: 0 !important;
    margin: 0 !important;
    opacity: 0 !important;
    pointer-events: none !important;
}

.ios-glass {
    background: rgba(255, 255, 255, 0.25) !important;
    border-radius: 20px !important;
    border: 1px solid rgba(255, 255, 255, 0.6) !important;
    box-shadow: 0 10px 35px rgba(0, 0, 0, 0.05) !important;
    padding: 24px !important;
    margin-bottom: 16px !important;
}

.ios-glass textarea {
    background: rgba(255, 255, 255, 0.4) !important;
    border-radius: 12px !important;
    border: 1px solid rgba(255, 255, 255, 0.3) !important;
}

.apple-btn {
    background: linear-gradient(135deg, #0071e3 0%, #00c6ff 100%) !important;
    color: white !important;
    border: none !important;
    border-radius: 14px !important;
    font-weight: 600 !important;
    box-shadow: 0 4px 15px rgba(0, 113, 227, 0.25) !important;
    height: 46px !important;
}

.minor-btn {
    background: rgba(255, 255, 255, 0.8) !important;
    color: #0071e3 !important;
    border: 1px solid rgba(0, 113, 227, 0.2) !important;
    border-radius: 12px !important;
}

@media (max-width: 768px) {
    #main-title h1 { font-size: 2.0rem !important; }
    .row {
        flex-direction: column !important;
        display: flex !important;
    }
    .column {
        width: 100% !important;
        max-width: 100% !important;
    }
    .ios-glass {
        padding: 18px !important;
        margin-bottom: 12px !important;
    }
}
"""

# ================= 4. UI 界面组装 =================
initial_grades = get_dynamic_grades()
default_grade = initial_grades[0]
initial_files = get_dynamic_files(default_grade)
default_file = [initial_files[0]] if initial_files else []
default_text = load_multiple_files_content(default_grade, default_file)

with gr.Blocks() as demo:
    
    with gr.Column(elem_id="main-title"):
        gr.Markdown("# ✍️ 听写助手")
        gr.Markdown("默默积累，静待花开之时！")
        
    with gr.Accordion("➕ 添加年级", open=False, elem_classes=["ios-glass"]):
        with gr.Row():
            new_grade_input = gr.Textbox(label="新建年级名称", placeholder="如：五年级下册", lines=1)
            btn_create_grade = gr.Button("创建新年级", elem_classes=["minor-btn"])
        with gr.Row():
            new_lesson_input = gr.Textbox(label="在新创/当前年级下建新课时", placeholder="直接写数字，如：20", lines=1)
            btn_create_lesson = gr.Button("创建新课时", elem_classes=["minor-btn"])
        info_output = gr.Markdown("")

    with gr.Row(equal_height=False):
        # ==================== 左侧：配置区 ====================
        with gr.Column(scale=1):
            with gr.Column(elem_classes=["ios-glass"]):
                gr.Markdown("### 🗂️ 课本源选择")
                with gr.Row():
                    grade_dropdown = gr.Dropdown(
                        choices=initial_grades,
                        value=default_grade,
                        label="选择年级",
                        interactive=True
                    )
                    file_dropdown = gr.Dropdown(
                        choices=initial_files,
                        value=default_file,
                        label="勾选课时（支持多选混合）",
                        multiselect=True,
                        interactive=True
                    )
                btn_select_all = gr.Button("✨ 一键选中当前年级全部课时", elem_classes=["minor-btn"])

            with gr.Column(elem_classes=["ios-glass"]):
                gr.Markdown("### 📝 听写字词表")
                input_text = gr.Textbox(
                    value=default_text,
                    label="当前准备听写的字词（可在框内临时修改、增删）",
                    placeholder="选中的课时字词会自动合并展现到这里...",
                    lines=5
                )
                shuffle_toggle = gr.Checkbox(
                    value=False,
                    label="🔀 开启打乱词语顺序"
                )

        # ==================== 右侧：声音与输出区 ====================
        with gr.Column(scale=1):
            with gr.Column(elem_classes=["ios-glass"]):
                gr.Markdown("### ⚙️ 老师发音微调")
                voice_select = gr.Dropdown(
                    choices=[
                        ("✅ 稳过女声：标准普通话女声 (晓晓 - 默认)", "zh-CN-XiaoxiaoNeural"),
                        ("✅ 稳过男声：自然故事男老师 (云希 - 压低音调)", "zh-CN-YunxiNeural"),
                        ("✅ 稳过女声：生动小说女老师 (晓伊 - 很有感情)", "zh-CN-XiaoyiNeural"),
                        ("✅ 稳过男声：沉稳老教师男声 (云健 - 严肃认真)", "zh-CN-YunjianNeural"),
                        ("✅ 稳过男声：新闻纪录片男声 (云扬 - 吐字正气)", "zh-CN-YunyangNeural")
                        
                    ],
                    value="zh-CN-XiaoxiaoNeural",
                    label="选择语文老师声线"
                )
                
                with gr.Row():
                    pitch_slider = gr.Slider(minimum=-50, maximum=30, value=-15, step=1, label="声音厚度 (Hz)")
                    pause_slider = gr.Slider(minimum=1.0, maximum=4.0, value=1.5, step=0.1, label="重复间隔 (秒)")
                    speed_slider = gr.Slider(minimum=0.6, maximum=1.0, value=0.85, step=0.05, label="朗读语速")
                    
                btn_generate = gr.Button("📢 立即生成听写音频", elem_classes=["apple-btn"])
                
            # 【独立右下卡片】：干净简洁无波形控制器
            with gr.Column(elem_classes=["ios-glass"]):
                gr.Markdown("### 🎧 听写音频输出")
                output_audio = gr.Audio(label="播放器", type="filepath")

    # ================= 5. 下拉菜单数据核心联动 =================
    grade_dropdown.change(
        fn=on_grade_change,
        inputs=grade_dropdown,
        outputs=[file_dropdown, input_text]
    )
    
    file_dropdown.change(
        fn=load_multiple_files_content,
        inputs=[grade_dropdown, file_dropdown],
        outputs=input_text
    )
    
    btn_select_all.click(
        fn=on_select_all_click,
        inputs=grade_dropdown,
        outputs=[file_dropdown, input_text]
    )
    
    btn_create_grade.click(
        fn=create_new_grade_folder,
        inputs=new_grade_input,
        outputs=[grade_dropdown, new_grade_input]
    )
    
    btn_create_lesson.click(
        fn=create_new_lesson_file,
        inputs=[grade_dropdown, new_lesson_input],
        outputs=[file_dropdown, new_lesson_input, info_output]
    )
    
    btn_generate.click(
        fn=process_dictation, 
        inputs=[input_text, voice_select, speed_slider, pause_slider, pitch_slider, shuffle_toggle], 
        outputs=output_audio
    )

if __name__ == "__main__":
    demo.launch(
        css=ios_glass_css, 
        theme=gr.themes.Soft(), 
        server_name="0.0.0.0",
        server_port=7860
    )
