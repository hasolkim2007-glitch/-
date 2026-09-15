import os
import threading
import datetime
import logging
from typing import List, Optional

import discord
from discord import app_commands
from discord.ext import commands
from flask import Flask
from waitress import serve

# ---------------------------------------------------------
# 1. Flask 서버 설정 (UptimeRobot / Render 포트 감지용)
# ---------------------------------------------------------
app = Flask(__name__)

# Flask/Werkzeug 관련 경고 로그 최소화
log = logging.getLogger('werkzeug')
log.setLevel(logging.ERROR)

@app.route('/')
def home():
    return "Bot is running!"

def run_flask():
    port = int(os.environ.get("PORT", 10000))
    serve(app, host='0.0.0.0', port=port)

def keep_alive():
    t = threading.Thread(target=run_flask)
    t.daemon = True
    t.start()


# ---------------------------------------------------------
# 2. 디스코드 봇 설정 & 전역 상태 변수
# ---------------------------------------------------------
KST = datetime.timezone(datetime.timedelta(hours=9))

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)

# 가장 최근에 생성된 인원체크 패널 View 저장 전역 변수
active_attendance_view: Optional['AttendanceView'] = None


# ---------------------------------------------------------
# 3. 인원체크 UI (View & Buttons)
# ---------------------------------------------------------
class AttendanceView(discord.ui.View):
    def __init__(self, title: str, start_time_obj: datetime.datetime, raw_time_str: str):
        super().__init__(timeout=None)  # 영구 유지 패널
        self.title = title
        self.start_time_obj = start_time_obj
        self.raw_time_str = raw_time_str
        self.participants: List[discord.Member] = []
        self.message: Optional[discord.Message] = None

    def build_embed(self) -> discord.Embed:
        """현재 참가 명단 상태를 반영한 임베드 생성"""
        embed = discord.Embed(
            title=f"📋 {self.title}",
            description=f"⏰ **시작 시간:** `{self.raw_time_str}`\n⚠️ **주의:** 시작 20분 전부터는 버튼으로 직접 취소가 불가능합니다.",
            color=discord.Color.blue()
        )
        
        count = len(self.participants)
        if count > 0:
            user_list_str = "\n".join([f"{i+1}. {user.mention}" for i, user in enumerate(self.participants)])
        else:
            user_list_str = "현재 참가자가 없습니다."

        embed.add_field(name=f"👥 참가 명단 ({count}명)", value=user_list_str, inline=False)
        embed.set_footer(text="버튼을 눌러 참가를 신청하거나 취소할 수 있습니다.")
        return embed

    @discord.ui.button(label="참가하기", style=discord.ButtonStyle.success, custom_id="attend_btn")
    async def join_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        user = interaction.user
        if user in self.participants:
            await interaction.response.send_message("❌ 이미 참가 명단에 등록되어 있습니다.", ephemeral=True)
            return

        self.participants.append(user)
        await interaction.response.edit_message(embed=self.build_embed(), view=self)
        await interaction.followup.send(f"✅ {user.mention} 님이 참가 신청했습니다.", ephemeral=True)

    @discord.ui.button(label="참가 취소", style=discord.ButtonStyle.danger, custom_id="cancel_btn")
    async def cancel_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        user = interaction.user
        
        if user not in self.participants:
            await interaction.response.send_message("❌ 참가 명단에 없습니다.", ephemeral=True)
            return

        # 마감 시간 체크 (시작 20분 전부터 취소 불가)
        now = datetime.datetime.now(KST)
        time_diff = (self.start_time_obj - now).total_seconds() / 60.0

        if time_diff <= 20:
            await interaction.response.send_message(
                "❌ **시작 20분 전부터는 직접 취소할 수 없습니다.**\n취소가 필요한 경우 운영진/관리자에게 문의해 주세요.",
                ephemeral=True
            )
            return

        self.participants.remove(user)
        await interaction.response.edit_message(embed=self.build_embed(), view=self)
        await interaction.followup.send(f"⚠️ {user.mention} 님이 참가를 취소했습니다.", ephemeral=True)


# ---------------------------------------------------------
# 4. 봇 이벤트 설정
# ---------------------------------------------------------
@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} (ID: {bot.user.id})")
    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} command(s)")
    except Exception as e:
        print(f"Failed to sync commands: {e}")


# ---------------------------------------------------------
# 5. 슬래시 명령어 정의
# ---------------------------------------------------------

# --- 명령어 1: /인원체크 ---
@bot.tree.command(name="인원체크", description="참가/취소 인원 체크 패널을 생성합니다. (20분 전 취소 제한)")
@app_commands.describe(제목="예: 오늘 내전 참가자 모집", 시작시간="HH:MM 형식 입력 (예: 21:00 또는 21:30)")
@app_commands.checks.has_permissions(administrator=True)
async def attendance_panel(interaction: discord.Interaction, 제목: str, 시작시간: str):
    global active_attendance_view

    # 시간 파싱
    try:
        parsed_time = datetime.datetime.strptime(시작시간.strip(), "%H:%M").time()
        now = datetime.datetime.now(KST)
        start_time_obj = datetime.datetime.combine(now.date(), parsed_time).replace(tzinfo=KST)
    except ValueError:
        await interaction.response.send_message(
            "❌ **시간 형식이 올바르지 않습니다.**\n`21:00` 또는 `09:30`처럼 **HH:MM** 형식으로 입력해 주세요.",
            ephemeral=True
        )
        return

    # View 및 임베드 생성
    view = AttendanceView(title=제목, start_time_obj=start_time_obj, raw_time_str=시작시간)
    embed = view.build_embed()

    await interaction.response.send_message("인원 체크 패널이 생성되었습니다.", ephemeral=True)
    sent_msg = await interaction.channel.send(embed=embed, view=view)

    # 객체 참조 연결
    view.message = sent_msg
    active_attendance_view = view

@attendance_panel.error
async def attendance_panel_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.MissingPermissions):
        await interaction.response.send_message("❌ 이 명령어를 사용할 권한(관리자)이 없습니다.", ephemeral=True)


# --- 명령어 2: /강제취소 (관리자 전용) ---
@bot.tree.command(name="강제취소", description="[관리자 전용] 특정 유저를 참가 명단에서 강제로 제외합니다.")
@app_commands.describe(유저="명단에서 제외할 유저를 선택하세요.")
@app_commands.checks.has_permissions(administrator=True)
async def force_cancel_user(interaction: discord.Interaction, 유저: discord.Member):
    global active_attendance_view

    if active_attendance_view is None:
        await interaction.response.send_message("❌ 현재 진행 중인 인원체크 패널이 없습니다.", ephemeral=True)
        return

    if 유저 not in active_attendance_view.participants:
        await interaction.response.send_message(f"❌ {유저.mention} 님은 현재 참가 명단에 없습니다.", ephemeral=True)
        return

    # 명단에서 강제 제외
    active_attendance_view.participants.remove(유저)

    # 패널 메세지 자동 업데이트
    if active_attendance_view.message:
        try:
            await active_attendance_view.message.edit(
                embed=active_attendance_view.build_embed(),
                view=active_attendance_view
            )
        except Exception as e:
            print(f"패널 업데이트 오류: {e}")

    await interaction.response.send_message(
        f"✅ 관리자 권한으로 {유저.mention} 님을 참가 명단에서 강제 제외했습니다.",
        ephemeral=True
    )

@force_cancel_user.error
async def force_cancel_user_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.MissingPermissions):
        await interaction.response.send_message("❌ 이 명령어를 사용할 권한(관리자)이 없습니다.", ephemeral=True)


# ---------------------------------------------------------
# 6. 실행 구문
# ---------------------------------------------------------
if __name__ == "__main__":
    keep_alive()  # 웹 서버 실행
    
    TOKEN = os.environ.get("DISCORD_TOKEN")
    if not TOKEN:
        print("ERROR: DISCORD_TOKEN 환경변수가 설정되지 않았습니다.")
    else:
        bot.run(TOKEN)
