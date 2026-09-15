import os
import sys
import asyncio
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
# 1. Flask 서버 설정 (Render 포트 감지 및 UptimeRobot용)
# ---------------------------------------------------------
app = Flask(__name__)

# Flask 로그 출력 최소화
log = logging.getLogger('werkzeug')
log.setLevel(logging.ERROR)

@app.route('/')
def home():
    return "Bot is running!"

def run_flask():
    port = int(os.environ.get("PORT", 10000))
    print(f"Starting Flask server on port {port}...")
    serve(app, host='0.0.0.0', port=port)

def keep_alive():
    t = threading.Thread(target=run_flask)
    t.daemon = True
    t.start()


# ---------------------------------------------------------
# 2. 디스코드 봇 설정 & 전역 상태 변수
# ---------------------------------------------------------
KST = datetime.timezone(datetime.timedelta(hours=9))

# 운영진 로그 채널 ID (환경변수 설정 또는 기본값 0)
LOG_CHANNEL_ID = int(os.environ.get("LOG_CHANNEL_ID", 0))

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)

# 전역 상태 변수
active_attendance_view: Optional['AttendanceView'] = None
active_rule_vote_view: Optional['RuleVoteView'] = None
current_view: Optional['FirstComeLineView'] = None


# ---------------------------------------------------------
# 3. UI 클래스 1: 인원체크 UI (View & Buttons)
# ---------------------------------------------------------
class AttendanceView(discord.ui.View):
    def __init__(self, title: str, start_time_obj: datetime.datetime, raw_time_str: str):
        super().__init__(timeout=None)
        self.title = title
        self.start_time_obj = start_time_obj
        self.raw_time_str = raw_time_str
        self.participants: List[discord.Member] = []
        self.message: Optional[discord.Message] = None

    def build_embed(self) -> discord.Embed:
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
# 4. UI 클래스 2: 선착순 리롤/팀폭 신청 UI
# ---------------------------------------------------------
class FirstComeLineView(discord.ui.View):
    def __init__(self, disabled_initial=True):
        super().__init__(timeout=None)
        self.is_closed = False
        self.clicked_user = None
        self.message: Optional[discord.Message] = None
        
        if disabled_initial:
            for child in self.children:
                child.disabled = True

    def set_all_buttons_disabled(self, disabled_state: bool):
        for child in self.children:
            child.disabled = disabled_state

    async def handle_selection(self, interaction: discord.Interaction, selection_name: str):
        if self.is_closed:
            await interaction.response.send_message(
                "❌ **이미 리롤이 마감되었습니다!**",
                ephemeral=True
            )
            return

        # 선착순 선점
        self.is_closed = True
        self.clicked_user = interaction.user
        self.set_all_buttons_disabled(True)

        # 결과 Embed 생성
        embed = discord.Embed(
            title="✅ 리롤 신청 마감",
            description=f"🔥 **[{selection_name}] 리롤 신청 성공:** ",
            color=discord.Color.blue()
        )

        # 메시지 원본 업데이트 (버튼 비활성화 및 Embed 교체)
        await interaction.response.edit_message(embed=embed, view=self)
        await interaction.followup.send(f"✅ **[ {selection_name} ] 리롤 신청에 성공하셨습니다.**", ephemeral=True)

        # 운영진 로그 채널 전송
        if LOG_CHANNEL_ID != 1549301300053811290 and interaction.guild:
            try:
                log_channel = await interaction.client.fetch_channel(int(LOG_CHANNEL_ID))
                if log_channel:
                    now_str = datetime.datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
                    user = interaction.user
                    
                    embed_log = discord.Embed(
                        title="리롤/팀폭 신청 성공",
                        color=discord.Color.gold(),
                        timestamp=datetime.datetime.now(datetime.timezone.utc)
                    )
                    embed_log.add_field(name="신청 항목", value=f"**{selection_name}**", inline=True)
                    embed_log.add_field(name="당첨자", value=f"{user.mention} (`{user.display_name}`)", inline=True)
                    embed_log.add_field(name="유저 ID", value=f"`{user.id}`", inline=False)
                    embed_log.add_field(name="접수 시각", value=f"`{now_str}`", inline=False)

                    await log_channel.send(embed=embed_log)
            except Exception as e:
                print(f"[Error] 로그 전송 실패: {e}")

    @discord.ui.button(label="1라인", style=discord.ButtonStyle.primary, custom_id="line_1", row=0)
    async def line_1_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.handle_selection(interaction, "1라인")

    @discord.ui.button(label="2라인", style=discord.ButtonStyle.primary, custom_id="line_2", row=0)
    async def line_2_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.handle_selection(interaction, "2라인")

    @discord.ui.button(label="3라인", style=discord.ButtonStyle.primary, custom_id="line_3", row=0)
    async def line_3_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.handle_selection(interaction, "3라인")

    @discord.ui.button(label="4라인", style=discord.ButtonStyle.primary, custom_id="line_4", row=0)
    async def line_4_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.handle_selection(interaction, "4라인")

    @discord.ui.button(label="라인별팀폭", style=discord.ButtonStyle.danger, custom_id="team_bomb_line", row=1)
    async def line_bomb_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.handle_selection(interaction, "라인별팀폭")

    @discord.ui.button(label="올랜팀폭", style=discord.ButtonStyle.danger, custom_id="team_bomb_allrand", row=1)
    async def allrand_bomb_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.handle_selection(interaction, "올랜팀폭")

# ---------------------------------------------------------
# 5. UI 클래스 3: 라운드별 규칙 투표 UI
# ---------------------------------------------------------
class RuleVoteView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.pool = {
            "1라": ["올랜팀폭", "라인별팀폭"],
            "2라": ["선착순 1명", "일반전"],
            "3라": ["올랜팀폭", "라인별팀폭"],
            "4라": ["선착순 1명", "일반전"]
        }
        self.votes = {
            round_key: {option: set() for option in options}
            for round_key, options in self.pool.items()
        }
        self.message: Optional[discord.Message] = None

    def build_embed(self) -> discord.Embed:
        embed = discord.Embed(
            title="🎯 라운드별 규칙 투표 패널",
            description="아래 버튼을 눌러 라운드별 원하는 규칙에 투표하세요!",
            color=discord.Color.gold()
        )
        
        for round_name, options in self.votes.items():
            field_val = ""
            for opt, voters in options.items():
                field_val += f"• **{opt}**: {len(voters)}표\n"
            embed.add_field(name=f"📌 {round_name}", value=field_val, inline=False)

        embed.set_footer(text="1인당 라운드별 1표씩 투표 가능합니다.")
        return embed

    @discord.ui.button(label="1라: 올랜팀폭", style=discord.ButtonStyle.primary, custom_id="v1_opt1", row=0)
    async def v1_o1(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._vote(interaction, "1라", "올랜팀폭")

    @discord.ui.button(label="1라: 라인별팀폭", style=discord.ButtonStyle.primary, custom_id="v1_opt2", row=0)
    async def v1_o2(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._vote(interaction, "1라", "라인별팀폭")

    @discord.ui.button(label="2라: 선착순 1명", style=discord.ButtonStyle.secondary, custom_id="v2_opt1", row=1)
    async def v2_o1(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._vote(interaction, "2라", "선착순 1명")

    @discord.ui.button(label="2라: 일반전", style=discord.ButtonStyle.secondary, custom_id="v2_opt2", row=1)
    async def v2_o2(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._vote(interaction, "2라", "일반전")

    @discord.ui.button(label="3라: 올랜팀폭", style=discord.ButtonStyle.primary, custom_id="v3_opt1", row=2)
    async def v3_o1(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._vote(interaction, "3라", "올랜팀폭")

    @discord.ui.button(label="3라: 라인별팀폭", style=discord.ButtonStyle.primary, custom_id="v3_opt2", row=2)
    async def v3_o2(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._vote(interaction, "3라", "라인별팀폭")

    @discord.ui.button(label="4라: 선착순 1명", style=discord.ButtonStyle.secondary, custom_id="v4_opt1", row=3)
    async def v4_o1(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._vote(interaction, "4라", "선착순 1명")

    @discord.ui.button(label="4라: 일반전", style=discord.ButtonStyle.secondary, custom_id="v4_opt2", row=3)
    async def v4_o2(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._vote(interaction, "4라", "일반전")

    async def _vote(self, interaction: discord.Interaction, round_key: str, choice: str):
        user_id = interaction.user.id
        
        for opt in self.votes[round_key]:
            self.votes[round_key][opt].discard(user_id)

        self.votes[round_key][choice].add(user_id)

        await interaction.response.edit_message(embed=self.build_embed(), view=self)
        await interaction.followup.send(f"✅ [{round_key}] `{choice}` 에 투표하셨습니다.", ephemeral=True)


# 카운트다운 및 10초 대기 처리 함수
async def run_countdown_and_start(interaction: discord.Interaction, title_text: str):
    global current_view
    current_view = FirstComeLineView(disabled_initial=True)

    embed = discord.Embed(
        title=f"⏳ {title_text}",
        description="**카운트다운 진행 중... 잠시만 기다려주세요!**\n\n3️⃣",
        color=discord.Color.yellow()
    )
    
    msg = await interaction.channel.send(embed=embed, view=current_view)
    current_view.message = msg
    await interaction.response.send_message("카운트다운을 시작합니다.", ephemeral=True)

    await asyncio.sleep(1)
    embed.description = "**카운트다운 진행 중... 잠시만 기다려주세요!**\n\n2️⃣"
    await msg.edit(embed=embed)

    await asyncio.sleep(1)
    embed.description = "**카운트다운 진행 중... 잠시만 기다려주세요!**\n\n1️⃣"
    await msg.edit(embed=embed)

    await asyncio.sleep(1)
    
    current_view.set_all_buttons_disabled(False)
    embed.title = f"⚡ {title_text}"
    embed.description = "🔥 **신청 시작!! (남은 시간: 10초)**"
    embed.color = discord.Color.green()
    await msg.edit(embed=embed, view=current_view)

    for remaining in range(9, -1, -1):
        await asyncio.sleep(1)
        if current_view.is_closed:
            return
        
        if remaining > 0:
            embed.description = f"🔥 **신청 시작!! (남은 시간: {remaining}초)**"
            await msg.edit(embed=embed)

    if not current_view.is_closed:
        current_view.is_closed = True
        current_view.set_all_buttons_disabled(True)
        
        embed.title = f"⏰ {title_text} (마감)"
        embed.description = "⏱️ **10초 동안 신청이 없어 자동으로 마감되었습니다.**"
        embed.color = discord.Color.dark_gray()
        await msg.edit(embed=embed, view=current_view)


# ---------------------------------------------------------
# 6. 봇 이벤트 설정
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
# 7. 슬래시 명령어 정의
# ---------------------------------------------------------

# --- 1) /리롤 ---
@bot.tree.command(name="리롤", description="카운트다운 후 리롤 신청 버튼을 오픈합니다. (10초 제한)")
@app_commands.checks.has_permissions(administrator=True)
async def create_panel(interaction: discord.Interaction):
    await run_countdown_and_start(interaction, "리롤 / 팀폭 신청")


# --- 2) /인원체크 ---
@bot.tree.command(name="인원체크", description="참가/취소 인원 체크 패널을 생성합니다. (20분 전 취소 제한)")
@app_commands.describe(제목="예: 오늘 내전 참가자 모집", 시작시간="HH:MM 형식 입력 (예: 21:00 또는 21:30)")
@app_commands.checks.has_permissions(administrator=True)
async def attendance_panel(interaction: discord.Interaction, 제목: str, 시작시간: str):
    global active_attendance_view

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

    view = AttendanceView(title=제목, start_time_obj=start_time_obj, raw_time_str=시작시간)
    embed = view.build_embed()

    await interaction.response.send_message("인원 체크 패널이 생성되었습니다.", ephemeral=True)
    sent_msg = await interaction.channel.send(embed=embed, view=view)

    view.message = sent_msg
    active_attendance_view = view

@attendance_panel.error
async def attendance_panel_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.MissingPermissions):
        await interaction.response.send_message("❌ 이 명령어를 사용할 권한(관리자)이 없습니다.", ephemeral=True)


# --- 3) /강제취소 (관리자 전용) ---
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

    active_attendance_view.participants.remove(유저)

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


# --- 4) /투표패널 (1라~4라 규칙 투표 생성) ---
@bot.tree.command(name="투표패널", description="1라~4라 규칙 투표 패널을 채널에 생성합니다.")
@app_commands.checks.has_permissions(administrator=True)
async def create_vote_panel(interaction: discord.Interaction):
    global active_rule_vote_view

    view = RuleVoteView()
    embed = view.build_embed()

    await interaction.response.send_message("투표 패널이 생성되었습니다.", ephemeral=True)
    sent_msg = await interaction.channel.send(embed=embed, view=view)

    view.message = sent_msg
    active_rule_vote_view = view

@create_vote_panel.error
async def create_vote_panel_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.MissingPermissions):
        await interaction.response.send_message("❌ 이 명령어를 사용할 권한(관리자)이 없습니다.", ephemeral=True)


# --- 5) /청소 (메시지 대량 삭제) ---
@bot.tree.command(name="청소", description="[관리자 전용] 지정한 개수만큼 채널의 메시지를 삭제합니다.")
@app_commands.describe(개수="삭제할 메시지 개수 (1~100)")
@app_commands.checks.has_permissions(administrator=True)
async def clear_messages(interaction: discord.Interaction, 개수: int):
    if 개수 < 1 or 개수 > 100:
        await interaction.response.send_message("❌ 1개 이상 100개 이하의 개수를 입력해 주세요.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)
    deleted = await interaction.channel.purge(limit=개수)
    await interaction.followup.send(f"🧹 `{len(deleted)}`개의 메시지를 삭제했습니다.", ephemeral=True)

@clear_messages.error
async def clear_messages_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.MissingPermissions):
        await interaction.response.send_message("❌ 이 명령어를 사용할 권한(관리자)이 없습니다.", ephemeral=True)


# ---------------------------------------------------------
# 8. 실행 구문
# ---------------------------------------------------------
if __name__ == "__main__":
    keep_alive()

    TOKEN = os.environ.get("DISCORD_TOKEN")
    if not TOKEN:
        print("CRITICAL ERROR: DISCORD_TOKEN 환경변수가 설정되지 않았습니다.", file=sys.stderr)
        sys.exit(1)

    try:
        bot.run(TOKEN)
    except Exception as e:
        print(f"CRITICAL ERROR: Bot failed to run: {e}", file=sys.stderr)
        sys.exit(1)
