import os
import sys
import random
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

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)

# 전역 상태 변수
active_attendance_view: Optional['AttendanceView'] = None
active_rule_vote_view: Optional['RuleVoteView'] = None


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
# 4. UI 클래스 2: 라운드별 규칙 투표 & 리롤 UI
# ---------------------------------------------------------
class RuleVoteView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        # 규칙 데이터 정의
        self.pool = {
            "1라": ["올랜팀폭", "라인별팀폭"],
            "2라": ["선착순 1명", "일반전"],
            "3라": ["올랜팀폭", "라인별팀폭"],
            "4라": ["선착순 1명", "일반전"]
        }
        # 라운드별 현재 선택 항목 및 투표수 { '1라': {'올랜팀폭': set(user_id)}, ... }
        self.votes = {
            round_key: {option: set() for option in options}
            for round_key, options in self.pool.items()
        }
        self.message: Optional[discord.Message] = None

    def reroll(self):
        """규칙을 무작위로 다시 섞고 투표수 초기화"""
        self.votes = {
            round_key: {option: set() for option in options}
            for round_key, options in self.pool.items()
        }

    def build_embed(self) -> discord.Embed:
        embed = discord.Embed(
            title="🎯 라운드별 규칙 투표 패널",
            description="아래 버튼을 눌러 라운드별 원하는 규칙에 투표하세요!\n관리자는 `/리롤` 명령어로 규칙을 초기화/다시 뽑을 수 있습니다.",
            color=discord.Color.gold()
        )
        
        for round_name, options in self.votes.items():
            field_val = ""
            for opt, voters in options.items():
                field_val += f"• **{opt}**: {len(voters)}표\n"
            embed.add_field(name=f"📌 {round_name}", value=field_val, inline=False)

        embed.set_footer(text="1인당 라운드별 1표씩 투표 가능합니다.")
        return embed

    # --- 1라운드 투표 버튼 ---
    @discord.ui.button(label="1라: 올랜팀폭", style=discord.ButtonStyle.primary, custom_id="v1_opt1", row=0)
    async def v1_o1(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._vote(interaction, "1라", "올랜팀폭")

    @discord.ui.button(label="1라: 라인별팀폭", style=discord.ButtonStyle.primary, custom_id="v1_opt2", row=0)
    async def v1_o2(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._vote(interaction, "1라", "라인별팀폭")

    # --- 2라운드 투표 버튼 ---
    @discord.ui.button(label="2라: 선착순 1명", style=discord.ButtonStyle.secondary, custom_id="v2_opt1", row=1)
    async def v2_o1(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._vote(interaction, "2라", "선착순 1명")

    @discord.ui.button(label="2라: 일반전", style=discord.ButtonStyle.secondary, custom_id="v2_opt2", row=1)
    async def v2_o2(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._vote(interaction, "2라", "일반전")

    # --- 3라운드 투표 버튼 ---
    @discord.ui.button(label="3라: 올랜팀폭", style=discord.ButtonStyle.primary, custom_id="v3_opt1", row=2)
    async def v3_o1(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._vote(interaction, "3라", "올랜팀폭")

    @discord.ui.button(label="3라: 라인별팀폭", style=discord.ButtonStyle.primary, custom_id="v3_opt2", row=2)
    async def v3_o2(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._vote(interaction, "3라", "라인별팀폭")

    # --- 4라운드 투표 버튼 ---
    @discord.ui.button(label="4라: 선착순 1명", style=discord.ButtonStyle.secondary, custom_id="v4_opt1", row=3)
    async def v4_o1(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._vote(interaction, "4라", "선착순 1명")

    @discord.ui.button(label="4라: 일반전", style=discord.ButtonStyle.secondary, custom_id="v4_opt2", row=3)
    async def v4_o2(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._vote(interaction, "4라", "일반전")

    async def _vote(self, interaction: discord.Interaction, round_key: str, choice: str):
        user_id = interaction.user.id
        
        # 같은 라운드 내 기존 투표 제거 (중복 투표 방지)
        for opt in self.votes[round_key]:
            self.votes[round_key][opt].discard(user_id)

        # 새 항목에 투표
        self.votes[round_key][choice].add(user_id)

        await interaction.response.edit_message(embed=self.build_embed(), view=self)
        await interaction.followup.send(f"✅ [{round_key}] `{choice}` 에 투표하셨습니다.", ephemeral=True)


# ---------------------------------------------------------
# 5. 봇 이벤트 설정
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
# 6. 슬래시 명령어 정의
# ---------------------------------------------------------

# --- 1) /인원체크 ---
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


# --- 2) /강제취소 (관리자 전용) ---
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


# --- 3) /투표패널 (1라~4라 규칙 투표 생성) ---
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


# --- 4) /리롤 (규칙 투표 초기화 및 패널 리셋) ---
@bot.tree.command(name="리롤", description="[관리자 전용] 규칙 투표 표수를 초기화하고 패널을 리셋합니다.")
@app_commands.checks.has_permissions(administrator=True)
async def reroll_vote_panel(interaction: discord.Interaction):
    global active_rule_vote_view

    if active_rule_vote_view is None:
        await interaction.response.send_message("❌ 활성화된 규칙 투표 패널이 없습니다. 먼저 `/투표패널`을 생성해 주세요.", ephemeral=True)
        return

    # 투표 상태 초기화
    active_rule_vote_view.reroll()

    # 패널 화면 갱신
    if active_rule_vote_view.message:
        try:
            await active_rule_vote_view.message.edit(
                embed=active_rule_vote_view.build_embed(),
                view=active_rule_vote_view
            )
        except Exception as e:
            print(f"투표 패널 업데이트 오류: {e}")

    await interaction.response.send_message("🔄 규칙 투표가 초기화(리롤)되었습니다.", ephemeral=True)

@reroll_vote_panel.error
async def reroll_vote_panel_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
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
# 7. 실행 구문
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
