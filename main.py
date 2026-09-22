import os
import sys
import time
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


# =========================================================
# 1. Flask 서버 설정
# =========================================================

app = Flask(__name__)

log = logging.getLogger("werkzeug")
log.setLevel(logging.ERROR)


@app.route("/")
def home():
    return "Bot is running!"


def run_flask():
    port = int(os.environ.get("PORT", 10000))
    print(f"Starting Flask server on port {port}...")
    serve(app, host="0.0.0.0", port=port)


def keep_alive():
    t = threading.Thread(target=run_flask)
    t.daemon = True
    t.start()


# =========================================================
# 2. 기본 설정
# =========================================================

KST = datetime.timezone(datetime.timedelta(hours=9))

LOG_CHANNEL_ID = int(os.environ.get("LOG_CHANNEL_ID", 0))

intents = discord.Intents.default()
intents.message_content = True


# =========================================================
# 3. 봇 설정
# =========================================================

class CustomBot(commands.Bot):

    async def setup_hook(self):
        try:
            synced = await self.tree.sync()
            print(f"Synced {len(synced)} command(s)")
        except Exception as e:
            print(f"Failed to sync commands: {e}")


bot = CustomBot(
    command_prefix="!",
    intents=intents
)


# =========================================================
# 4. 전역 상태
# =========================================================

active_attendance_view: Optional["AttendanceView"] = None
active_rule_vote_view: Optional["RuleVoteView"] = None
current_view = None


# =========================================================
# 5. 리롤 / 팀폭 신청 기록
# =========================================================

def get_empty_stats():
    return {
        "1라인": 0,
        "2라인": 0,
        "3라인": 0,
        "4라인": 0,

        "라인별팀폭": 0,
        "머리제외 올랜팀폭": 0,
        "머리포함 올랜팀폭": 0
    }


# 예:
#
# roll_stats = {
#     123456789: {
#         "1라인": 2,
#         "2라인": 1,
#         "3라인": 0,
#         "4라인": 0,
#         "라인별팀폭": 1,
#         "머리제외 올랜팀폭": 2,
#         "머리포함 올랜팀폭": 0
#     }
# }

roll_stats = {}


# =========================================================
# 6. 인원체크 UI
# =========================================================

class AttendanceView(discord.ui.View):

    def __init__(
        self,
        title: str,
        start_time_obj: datetime.datetime,
        raw_time_str: str
    ):
        super().__init__(timeout=None)

        self.title = title
        self.start_time_obj = start_time_obj
        self.raw_time_str = raw_time_str

        self.participants: List[discord.Member] = []
        self.message: Optional[discord.Message] = None

    def build_embed(self) -> discord.Embed:

        embed = discord.Embed(
            title=f"📋 {self.title}",
            description=(
                f"⏰ **시작 시간:** `{self.raw_time_str}`\n"
                f"⚠️ **주의:** 시작 20분 전부터는 "
                f"버튼으로 직접 취소가 불가능합니다."
            ),
            color=discord.Color.blue()
        )

        count = len(self.participants)

        if count > 0:

            user_list_str = "\n".join(
                [
                    f"{i + 1}. {user.mention}"
                    for i, user in enumerate(self.participants)
                ]
            )

        else:
            user_list_str = "현재 참가자가 없습니다."

        embed.add_field(
            name=f"👥 참가 명단 ({count}명)",
            value=user_list_str,
            inline=False
        )

        embed.set_footer(
            text="버튼을 눌러 참가를 신청하거나 취소할 수 있습니다."
        )

        return embed

    # -----------------------------------------------------
    # 참가하기
    # -----------------------------------------------------

    @discord.ui.button(
        label="참가하기",
        style=discord.ButtonStyle.success,
        custom_id="attend_btn"
    )
    async def join_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button
    ):

        user = interaction.user

        if user in self.participants:

            await interaction.response.send_message(
                "❌ 이미 참가 명단에 등록되어 있습니다.",
                ephemeral=True
            )

            return

        self.participants.append(user)

        await interaction.response.edit_message(
            embed=self.build_embed(),
            view=self
        )

        await interaction.followup.send(
            f"✅ {user.mention} 님이 참가 신청했습니다.",
            ephemeral=True
        )

    # -----------------------------------------------------
    # 참가 취소
    # -----------------------------------------------------

    @discord.ui.button(
        label="참가 취소",
        style=discord.ButtonStyle.danger,
        custom_id="cancel_btn"
    )
    async def cancel_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button
    ):

        user = interaction.user

        if user not in self.participants:

            await interaction.response.send_message(
                "❌ 참가 명단에 없습니다.",
                ephemeral=True
            )

            return

        now = datetime.datetime.now(KST)

        time_diff = (
            self.start_time_obj - now
        ).total_seconds() / 60.0

        if time_diff <= 20:

            await interaction.response.send_message(
                "❌ **시작 20분 전부터는 직접 취소할 수 없습니다.**\n"
                "취소가 필요한 경우 운영진/관리자에게 문의해 주세요.",
                ephemeral=True
            )

            return

        self.participants.remove(user)

        await interaction.response.edit_message(
            embed=self.build_embed(),
            view=self
        )

        await interaction.followup.send(
            f"⚠️ {user.mention} 님이 참가를 취소했습니다.",
            ephemeral=True
        )


# =========================================================
# 7. 리롤 UI
# =========================================================

class RollView(discord.ui.View):

    def __init__(self, disabled_initial=True):

        super().__init__(timeout=None)

        self.is_closed = False
        self.clicked_user = None
        self.message: Optional[discord.Message] = None

        if disabled_initial:
            self.set_all_buttons_disabled(True)

    def set_all_buttons_disabled(
        self,
        disabled_state: bool
    ):

        for child in self.children:
            child.disabled = disabled_state

    async def handle_selection(
        self,
        interaction: discord.Interaction,
        selection_name: str
    ):

        global roll_stats

        # -------------------------------------------------
        # 이미 마감
        # -------------------------------------------------

        if self.is_closed:

            await interaction.response.send_message(
                "❌ **이미 리롤 신청이 마감되었습니다!**",
                ephemeral=True
            )

            return

        # -------------------------------------------------
        # 선착순 마감
        # -------------------------------------------------

        self.is_closed = True
        self.clicked_user = interaction.user

        self.set_all_buttons_disabled(True)

        # -------------------------------------------------
        # 신청 기록
        # -------------------------------------------------

        user_id = interaction.user.id

        if user_id not in roll_stats:
            roll_stats[user_id] = get_empty_stats()

        roll_stats[user_id][selection_name] += 1

        # -------------------------------------------------
        # 결과 Embed
        # -------------------------------------------------

        embed = discord.Embed(
            title="✅ 리롤 신청 마감",
            description=(
                f"🔥 **[{selection_name}] "
                f"리롤 신청 성공!**"
            ),
            color=discord.Color.green()
        )

        await interaction.response.edit_message(
            embed=embed,
            view=self
        )

        await interaction.followup.send(
            f"✅ **[{selection_name}] "
            f"리롤 신청에 성공하셨습니다.**",
            ephemeral=True
        )

        # -------------------------------------------------
        # 운영진 로그
        # -------------------------------------------------

        await send_admin_log(
            interaction,
            "🎲 리롤 신청 성공",
            selection_name,
            discord.Color.gold()
        )


    # -----------------------------------------------------
    # 리롤 버튼
    # -----------------------------------------------------

    @discord.ui.button(
        label="1라인",
        style=discord.ButtonStyle.primary,
        custom_id="roll_1",
        row=0
    )
    async def roll_1(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button
    ):

        await self.handle_selection(
            interaction,
            "1라인"
        )


    @discord.ui.button(
        label="2라인",
        style=discord.ButtonStyle.primary,
        custom_id="roll_2",
        row=0
    )
    async def roll_2(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button
    ):

        await self.handle_selection(
            interaction,
            "2라인"
        )


    @discord.ui.button(
        label="3라인",
        style=discord.ButtonStyle.primary,
        custom_id="roll_3",
        row=0
    )
    async def roll_3(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button
    ):

        await self.handle_selection(
            interaction,
            "3라인"
        )


    @discord.ui.button(
        label="4라인",
        style=discord.ButtonStyle.primary,
        custom_id="roll_4",
        row=0
    )
    async def roll_4(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button
    ):

        await self.handle_selection(
            interaction,
            "4라인"
        )


# =========================================================
# 8. 팀폭 UI
# =========================================================

class TeamBombView(discord.ui.View):

    def __init__(self, disabled_initial=True):

        super().__init__(timeout=None)

        self.is_closed = False
        self.clicked_user = None
        self.message: Optional[discord.Message] = None

        if disabled_initial:
            self.set_all_buttons_disabled(True)

    def set_all_buttons_disabled(
        self,
        disabled_state: bool
    ):

        for child in self.children:
            child.disabled = disabled_state

    async def handle_selection(
        self,
        interaction: discord.Interaction,
        selection_name: str
    ):

        global roll_stats

        # -------------------------------------------------
        # 이미 마감
        # -------------------------------------------------

        if self.is_closed:

            await interaction.response.send_message(
                "❌ **이미 팀폭 신청이 마감되었습니다!**",
                ephemeral=True
            )

            return

        # -------------------------------------------------
        # 선착순 마감
        # -------------------------------------------------

        self.is_closed = True
        self.clicked_user = interaction.user

        self.set_all_buttons_disabled(True)

        # -------------------------------------------------
        # 신청 기록
        # -------------------------------------------------

        user_id = interaction.user.id

        if user_id not in roll_stats:
            roll_stats[user_id] = get_empty_stats()

        roll_stats[user_id][selection_name] += 1

        # -------------------------------------------------
        # 결과 Embed
        # -------------------------------------------------

        embed = discord.Embed(
            title="✅ 팀폭 신청 마감",
            description=(
                f"🔥 **[{selection_name}] "
                f"신청 성공!**"
            ),
            color=discord.Color.red()
        )

        await interaction.response.edit_message(
            embed=embed,
            view=self
        )

        await interaction.followup.send(
            f"✅ **[{selection_name}] "
            f"신청에 성공하셨습니다.**",
            ephemeral=True
        )

        # -------------------------------------------------
        # 운영진 로그
        # -------------------------------------------------

        await send_admin_log(
            interaction,
            "💥 팀폭 신청 성공",
            selection_name,
            discord.Color.red()
        )


    # -----------------------------------------------------
    # 라인별팀폭
    # -----------------------------------------------------

    @discord.ui.button(
        label="라인별팀폭",
        style=discord.ButtonStyle.danger,
        custom_id="bomb_line",
        row=0
    )
    async def bomb_line(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button
    ):

        await self.handle_selection(
            interaction,
            "라인별팀폭"
        )


    # -----------------------------------------------------
    # 머리제외 올랜팀폭
    # -----------------------------------------------------

    @discord.ui.button(
        label="머리제외 올랜팀폭",
        style=discord.ButtonStyle.danger,
        custom_id="bomb_all_no_head",
        row=1
    )
    async def bomb_all_no_head(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button
    ):

        await self.handle_selection(
            interaction,
            "머리제외 올랜팀폭"
        )


    # -----------------------------------------------------
    # 머리포함 올랜팀폭
    # -----------------------------------------------------

    @discord.ui.button(
        label="머리포함 올랜팀폭",
        style=discord.ButtonStyle.danger,
        custom_id="bomb_all_with_head",
        row=2
    )
    async def bomb_all_with_head(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button
    ):

        await self.handle_selection(
            interaction,
            "머리포함 올랜팀폭"
        )


# =========================================================
# 9. 운영진 로그 함수
# =========================================================

async def send_admin_log(
    interaction: discord.Interaction,
    title: str,
    selection_name: str,
    color: discord.Color
):

    if LOG_CHANNEL_ID == 0:
        return

    if not interaction.guild:
        return

    try:

        log_channel = await interaction.client.fetch_channel(
            int(LOG_CHANNEL_ID)
        )

        if log_channel:

            now_str = datetime.datetime.now(
                KST
            ).strftime(
                "%Y-%m-%d %H:%M:%S.%f"
            )[:-3]

            user = interaction.user

            embed_log = discord.Embed(
                title=title,
                color=color,
                timestamp=datetime.datetime.now(
                    datetime.timezone.utc
                )
            )

            embed_log.add_field(
                name="신청 항목",
                value=f"**{selection_name}**",
                inline=True
            )

            embed_log.add_field(
                name="신청자",
                value=(
                    f"{user.mention} "
                    f"(`{user.display_name}`)"
                ),
                inline=True
            )

            embed_log.add_field(
                name="유저 ID",
                value=f"`{user.id}`",
                inline=False
            )

            embed_log.add_field(
                name="접수 시각",
                value=f"`{now_str}`",
                inline=False
            )

            await log_channel.send(
                embed=embed_log
            )

    except Exception as e:

        print(
            f"[Error] 로그 전송 실패: {e}"
        )


# =========================================================
# 10. 라운드별 규칙 투표 UI
# =========================================================

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
            round_key: {
                option: set()
                for option in options
            }
            for round_key, options in self.pool.items()
        }

        self.message: Optional[discord.Message] = None

    def build_embed(self):

        embed = discord.Embed(
            title="🎯 라운드별 규칙 투표 패널",
            description=(
                "아래 버튼을 눌러 라운드별 "
                "원하는 규칙에 투표하세요!"
            ),
            color=discord.Color.gold()
        )

        for round_name, options in self.votes.items():

            field_val = ""

            for opt, voters in options.items():

                field_val += (
                    f"• **{opt}**: "
                    f"{len(voters)}표\n"
                )

            embed.add_field(
                name=f"📌 {round_name}",
                value=field_val,
                inline=False
            )

        embed.set_footer(
            text="1인당 라운드별 1표씩 투표 가능합니다."
        )

        return embed


    @discord.ui.button(
        label="1라: 올랜팀폭",
        style=discord.ButtonStyle.primary,
        custom_id="v1_opt1",
        row=0
    )
    async def v1_o1(
        self,
        interaction,
        button
    ):

        await self._vote(
            interaction,
            "1라",
            "올랜팀폭"
        )


    @discord.ui.button(
        label="1라: 라인별팀폭",
        style=discord.ButtonStyle.primary,
        custom_id="v1_opt2",
        row=0
    )
    async def v1_o2(
        self,
        interaction,
        button
    ):

        await self._vote(
            interaction,
            "1라",
            "라인별팀폭"
        )


    @discord.ui.button(
        label="2라: 선착순 1명",
        style=discord.ButtonStyle.secondary,
        custom_id="v2_opt1",
        row=1
    )
    async def v2_o1(
        self,
        interaction,
        button
    ):

        await self._vote(
            interaction,
            "2라",
            "선착순 1명"
        )


    @discord.ui.button(
        label="2라: 일반전",
        style=discord.ButtonStyle.secondary,
        custom_id="v2_opt2",
        row=1
    )
    async def v2_o2(
        self,
        interaction,
        button
    ):

        await self._vote(
            interaction,
            "2라",
            "일반전"
        )


    @discord.ui.button(
        label="3라: 올랜팀폭",
        style=discord.ButtonStyle.primary,
        custom_id="v3_opt1",
        row=2
    )
    async def v3_o1(
        self,
        interaction,
        button
    ):

        await self._vote(
            interaction,
            "3라",
            "올랜팀폭"
        )


    @discord.ui.button(
        label="3라: 라인별팀폭",
        style=discord.ButtonStyle.primary,
        custom_id="v3_opt2",
        row=2
    )
    async def v3_o2(
        self,
        interaction,
        button
    ):

        await self._vote(
            interaction,
            "3라",
            "라인별팀폭"
        )


    @discord.ui.button(
        label="4라: 선착순 1명",
        style=discord.ButtonStyle.secondary,
        custom_id="v4_opt1",
        row=3
    )
    async def v4_o1(
        self,
        interaction,
        button
    ):

        await self._vote(
            interaction,
            "4라",
            "선착순 1명"
        )


    @discord.ui.button(
        label="4라: 일반전",
        style=discord.ButtonStyle.secondary,
        custom_id="v4_opt2",
        row=3
    )
    async def v4_o2(
        self,
        interaction,
        button
    ):

        await self._vote(
            interaction,
            "4라",
            "일반전"
        )


    async def _vote(
        self,
        interaction,
        round_key,
        choice
    ):

        user_id = interaction.user.id

        for opt in self.votes[round_key]:
            self.votes[round_key][opt].discard(
                user_id
            )

        self.votes[round_key][choice].add(
            user_id
        )

        await interaction.response.edit_message(
            embed=self.build_embed(),
            view=self
        )

        await interaction.followup.send(
            f"✅ [{round_key}] "
            f"`{choice}` 에 투표하셨습니다.",
            ephemeral=True
        )


# =========================================================
# 11. 리롤 / 팀폭 카운트다운
# =========================================================

async def run_countdown_and_start(
    interaction: discord.Interaction,
    title_text: str,
    mode: str,
    duration: int
):

    global current_view

    # -----------------------------------------------------
    # View 선택
    # -----------------------------------------------------

    if mode == "roll":

        current_view = RollView(
            disabled_initial=True
        )

        mode_name = "리롤"

    else:

        current_view = TeamBombView(
            disabled_initial=True
        )

        mode_name = "팀폭"

    # -----------------------------------------------------
    # 최초 Embed
    # -----------------------------------------------------

    embed = discord.Embed(
        title=f"⏳ {title_text}",
        description=(
            "**카운트다운 진행 중... "
            "잠시만 기다려주세요!**\n\n"
            "3️⃣"
        ),
        color=discord.Color.yellow()
    )

    msg = await interaction.channel.send(
        embed=embed,
        view=current_view
    )

    current_view.message = msg

    await interaction.response.send_message(
        f"{mode_name} 카운트다운을 시작합니다.",
        ephemeral=True
    )

    # -----------------------------------------------------
    # 3 → 2
    # -----------------------------------------------------

    await asyncio.sleep(1)

    embed.description = (
        "**카운트다운 진행 중... "
        "잠시만 기다려주세요!**\n\n"
        "2️⃣"
    )

    await msg.edit(
        embed=embed
    )

    # -----------------------------------------------------
    # 2 → 1
    # -----------------------------------------------------

    await asyncio.sleep(1)

    embed.description = (
        "**카운트다운 진행 중... "
        "잠시만 기다려주세요!**\n\n"
        "1️⃣"
    )

    await msg.edit(
        embed=embed
    )

    # -----------------------------------------------------
    # 시작
    # -----------------------------------------------------

    await asyncio.sleep(1)

    current_view.set_all_buttons_disabled(
        False
    )

    end_time = int(
        time.time()
    ) + duration

    embed.title = f"⚡ {title_text}"

    embed.description = (
        f"🔥 **신청 시작!!**\n\n"
        f"⏱️ **마감까지 <t:{end_time}:R>**"
    )

    embed.color = discord.Color.green()

    await msg.edit(
        embed=embed,
        view=current_view
    )

    # -----------------------------------------------------
    # 대기
    # -----------------------------------------------------

    start_wait = time.time()

    while (
        time.time() - start_wait
        < duration
    ):

        if current_view.is_closed:
            return

        await asyncio.sleep(0.5)

    # -----------------------------------------------------
    # 시간 종료
    # -----------------------------------------------------

    if not current_view.is_closed:

        current_view.is_closed = True

        current_view.set_all_buttons_disabled(
            True
        )

        embed.title = (
            f"⏰ {title_text} (마감)"
        )

        embed.description = (
            f"⏱️ **{duration}초 동안 "
            f"신청이 없어 자동으로 마감되었습니다.**"
        )

        embed.color = discord.Color.dark_gray()

        await msg.edit(
            embed=embed,
            view=current_view
        )


# =========================================================
# 12. 봇 이벤트
# =========================================================

@bot.event
async def on_ready():

    print(
        f"Logged in as "
        f"{bot.user} "
        f"(ID: {bot.user.id})"
    )


# =========================================================
# 13. /리롤
# =========================================================

@bot.tree.command(
    name="리롤",
    description="카운트다운 후 리롤 신청을 15초간 오픈합니다."
)
@app_commands.checks.has_permissions(
    administrator=True
)
async def create_roll_panel(
    interaction: discord.Interaction
):

    await run_countdown_and_start(
        interaction,
        "리롤 신청",
        mode="roll",
        duration=15
    )


@create_roll_panel.error
async def create_roll_panel_error(
    interaction: discord.Interaction,
    error: app_commands.AppCommandError
):

    if isinstance(
        error,
        app_commands.MissingPermissions
    ):

        await interaction.response.send_message(
            "❌ 이 명령어를 사용할 권한(관리자)이 없습니다.",
            ephemeral=True
        )


# =========================================================
# 14. /팀폭
# =========================================================

@bot.tree.command(
    name="팀폭",
    description="카운트다운 후 팀폭 신청을 1분간 오픈합니다."
)
@app_commands.checks.has_permissions(
    administrator=True
)
async def create_team_bomb_panel(
    interaction: discord.Interaction
):

    await run_countdown_and_start(
        interaction,
        "팀폭 신청",
        mode="team",
        duration=60
    )


@create_team_bomb_panel.error
async def create_team_bomb_panel_error(
    interaction: discord.Interaction,
    error: app_commands.AppCommandError
):

    if isinstance(
        error,
        app_commands.MissingPermissions
    ):

        await interaction.response.send_message(
            "❌ 이 명령어를 사용할 권한(관리자)이 없습니다.",
            ephemeral=True
        )


# =========================================================
# 15. /리롤종료
# =========================================================

@bot.tree.command(
    name="리롤종료",
    description="리롤/팀폭 신청 횟수를 각 신청자에게 DM으로 전송합니다."
)
@app_commands.checks.has_permissions(
    administrator=True
)
async def finish_roll(
    interaction: discord.Interaction
):

    global roll_stats
    global current_view

    # -----------------------------------------------------
    # 기록 없음
    # -----------------------------------------------------

    if not roll_stats:

        await interaction.response.send_message(
            "❌ 현재까지 리롤/팀폭 신청 기록이 없습니다.",
            ephemeral=True
        )

        return

    await interaction.response.defer(
        ephemeral=True
    )

    dm_success = 0
    dm_fail = 0

    # -----------------------------------------------------
    # 모든 신청자에게 DM
    # -----------------------------------------------------

    for user_id, stats in list(
        roll_stats.items()
    ):

        total = sum(
            stats.values()
        )

        if total <= 0:
            continue

        try:

            user = await bot.fetch_user(
                user_id
            )

            dm_embed = discord.Embed(
                title="📊 리롤 / 팀폭 신청 결과",
                description=(
                    "이번 리롤/팀폭 이벤트에서\n"
                    "회원님의 신청 횟수를 알려드립니다."
                ),
                color=discord.Color.blue()
            )

            # -------------------------------------------------
            # 리롤
            # -------------------------------------------------

            dm_embed.add_field(
                name="🎲 리롤",
                value=(
                    f"1라인: **{stats['1라인']}회**\n"
                    f"2라인: **{stats['2라인']}회**\n"
                    f"3라인: **{stats['3라인']}회**\n"
                    f"4라인: **{stats['4라인']}회**"
                ),
                inline=False
            )

            # -------------------------------------------------
            # 팀폭
            # -------------------------------------------------

            dm_embed.add_field(
                name="💥 팀폭",
                value=(
                    f"라인별팀폭: **{stats['라인별팀폭']}회**\n"
                    f"머리제외 올랜팀폭: "
                    f"**{stats['머리제외 올랜팀폭']}회**\n"
                    f"머리포함 올랜팀폭: "
                    f"**{stats['머리포함 올랜팀폭']}회**"
                ),
                inline=False
            )

            # -------------------------------------------------
            # 총합
            # -------------------------------------------------

            dm_embed.add_field(
                name="📌 총 신청 횟수",
                value=f"**{total}회**",
                inline=False
            )

            dm_embed.set_footer(
                text="리롤 / 팀폭 신청 집계 시스템"
            )

            await user.send(
                embed=dm_embed
            )

            dm_success += 1

        except discord.Forbidden:

            dm_fail += 1

            print(
                f"[DM 실패] {user_id}: "
                f"DM 차단 또는 비활성화"
            )

        except discord.HTTPException as e:

            dm_fail += 1

            print(
                f"[DM 실패] {user_id}: {e}"
            )

    # -----------------------------------------------------
    # 현재 패널 닫기
    # -----------------------------------------------------

    if current_view is not None:

        try:

            current_view.is_closed = True

            current_view.set_all_buttons_disabled(
                True
            )

            if current_view.message:

                await current_view.message.edit(
                    view=current_view
                )

        except Exception as e:

            print(
                f"패널 종료 처리 오류: {e}"
            )

    # -----------------------------------------------------
    # 중요:
    #
    # 여기서는 roll_stats.clear()를 하지 않습니다.
    #
    # /리롤종료를 다시 실행하면
    # 동일한 기록을 다시 DM할 수 있습니다.
    #
    # 실제 초기화는 /초기화에서 합니다.
    # -----------------------------------------------------

    await interaction.followup.send(
        (
            f"✅ **리롤/팀폭 결과 DM 전송 완료**\n\n"
            f"📨 DM 성공: `{dm_success}명`\n"
            f"❌ DM 실패: `{dm_fail}명`\n\n"
            f"💡 횟수 기록은 아직 유지되어 있습니다.\n"
            f"완전히 초기화하려면 `/초기화`를 사용하세요."
        ),
        ephemeral=True
    )


@finish_roll.error
async def finish_roll_error(
    interaction: discord.Interaction,
    error: app_commands.AppCommandError
):

    if isinstance(
        error,
        app_commands.MissingPermissions
    ):

        await interaction.response.send_message(
            "❌ 이 명령어를 사용할 권한(관리자)이 없습니다.",
            ephemeral=True
        )


# =========================================================
# 16. /초기화
# =========================================================

@bot.tree.command(
    name="초기화",
    description="누적된 리롤/팀폭 신청 횟수를 모두 초기화합니다."
)
@app_commands.checks.has_permissions(
    administrator=True
)
async def reset_roll_stats(
    interaction: discord.Interaction
):

    global roll_stats

    # -----------------------------------------------------
    # 기록 없음
    # -----------------------------------------------------

    if not roll_stats:

        await interaction.response.send_message(
            "ℹ️ 현재 초기화할 리롤/팀폭 기록이 없습니다.",
            ephemeral=True
        )

        return

    # -----------------------------------------------------
    # 초기화 전 통계
    # -----------------------------------------------------

    user_count = len(
        roll_stats
    )

    total_count = 0

    for stats in roll_stats.values():

        total_count += sum(
            stats.values()
        )

    # -----------------------------------------------------
    # 초기화
    # -----------------------------------------------------

    roll_stats.clear()

    # -----------------------------------------------------
    # 결과
    # -----------------------------------------------------

    await interaction.response.send_message(
        (
            "🧹 **리롤/팀폭 횟수 초기화 완료**\n\n"
            f"👥 기록된 유저: `{user_count}명`\n"
            f"📊 총 신청 횟수: `{total_count}회`\n\n"
            "이제부터 새로운 신청 횟수가 다시 집계됩니다."
        ),
        ephemeral=True
    )


@reset_roll_stats.error
async def reset_roll_stats_error(
    interaction: discord.Interaction,
    error: app_commands.AppCommandError
):

    if isinstance(
        error,
        app_commands.MissingPermissions
    ):

        await interaction.response.send_message(
            "❌ 이 명령어를 사용할 권한(관리자)이 없습니다.",
            ephemeral=True
        )


# =========================================================
# 17. /인원체크
# =========================================================

@bot.tree.command(
    name="인원체크",
    description="참가/취소 인원 체크 패널을 생성합니다. (20분 전 취소 제한)"
)
@app_commands.describe(
    제목="예: 오늘 내전 참가자 모집",
    시작시간="HH:MM 형식 입력 (예: 21:00 또는 21:30)"
)
@app_commands.checks.has_permissions(
    administrator=True
)
async def attendance_panel(
    interaction: discord.Interaction,
    제목: str,
    시작시간: str
):

    global active_attendance_view

    try:

        parsed_time = datetime.datetime.strptime(
            시작시간.strip(),
            "%H:%M"
        ).time()

        now = datetime.datetime.now(
            KST
        )

        start_time_obj = datetime.datetime.combine(
            now.date(),
            parsed_time
        ).replace(
            tzinfo=KST
        )

    except ValueError:

        await interaction.response.send_message(
            "❌ **시간 형식이 올바르지 않습니다.**\n"
            "`21:00` 또는 `09:30`처럼 "
            "**HH:MM** 형식으로 입력해 주세요.",
            ephemeral=True
        )

        return

    view = AttendanceView(
        title=제목,
        start_time_obj=start_time_obj,
        raw_time_str=시작시간
    )

    embed = view.build_embed()

    await interaction.response.send_message(
        "인원 체크 패널이 생성되었습니다.",
        ephemeral=True
    )

    sent_msg = await interaction.channel.send(
        embed=embed,
        view=view
    )

    view.message = sent_msg

    active_attendance_view = view


@attendance_panel.error
async def attendance_panel_error(
    interaction: discord.Interaction,
    error: app_commands.AppCommandError
):

    if isinstance(
        error,
        app_commands.MissingPermissions
    ):

        await interaction.response.send_message(
            "❌ 이 명령어를 사용할 권한(관리자)이 없습니다.",
            ephemeral=True
        )


# =========================================================
# 18. /강제취소
# =========================================================

@bot.tree.command(
    name="강제취소",
    description="[관리자 전용] 특정 유저를 참가 명단에서 강제로 제외합니다."
)
@app_commands.describe(
    유저="명단에서 제외할 유저를 선택하세요."
)
@app_commands.checks.has_permissions(
    administrator=True
)
async def force_cancel_user(
    interaction: discord.Interaction,
    유저: discord.Member
):

    global active_attendance_view

    if active_attendance_view is None:

        await interaction.response.send_message(
            "❌ 현재 진행 중인 인원체크 패널이 없습니다.",
            ephemeral=True
        )

        return

    if 유저 not in active_attendance_view.participants:

        await interaction.response.send_message(
            f"❌ {유저.mention} 님은 "
            f"현재 참가 명단에 없습니다.",
            ephemeral=True
        )

        return

    active_attendance_view.participants.remove(
        유저
    )

    if active_attendance_view.message:

        try:

            await active_attendance_view.message.edit(
                embed=active_attendance_view.build_embed(),
                view=active_attendance_view
            )

        except Exception as e:

            print(
                f"패널 업데이트 오류: {e}"
            )

    await interaction.response.send_message(
        f"✅ 관리자 권한으로 "
        f"{유저.mention} 님을 참가 명단에서 "
        f"강제 제외했습니다.",
        ephemeral=True
    )


@force_cancel_user.error
async def force_cancel_user_error(
    interaction: discord.Interaction,
    error: app_commands.AppCommandError
):

    if isinstance(
        error,
        app_commands.MissingPermissions
    ):

        await interaction.response.send_message(
            "❌ 이 명령어를 사용할 권한(관리자)이 없습니다.",
            ephemeral=True
        )


# =========================================================
# 19. /투표패널
# =========================================================

@bot.tree.command(
    name="투표패널",
    description="1라~4라 규칙 투표 패널을 채널에 생성합니다."
)
@app_commands.checks.has_permissions(
    administrator=True
)
async def create_vote_panel(
    interaction: discord.Interaction
):

    global active_rule_vote_view

    view = RuleVoteView()

    embed = view.build_embed()

    await interaction.response.send_message(
        "투표 패널이 생성되었습니다.",
        ephemeral=True
    )

    sent_msg = await interaction.channel.send(
        embed=embed,
        view=view
    )

    view.message = sent_msg

    active_rule_vote_view = view


@create_vote_panel.error
async def create_vote_panel_error(
    interaction: discord.Interaction,
    error: app_commands.AppCommandError
):

    if isinstance(
        error,
        app_commands.MissingPermissions
    ):

        await interaction.response.send_message(
            "❌ 이 명령어를 사용할 권한(관리자)이 없습니다.",
            ephemeral=True
        )


# =========================================================
# 20. /청소
# =========================================================

@bot.tree.command(
    name="청소",
    description="[관리자 전용] 지정한 개수만큼 채널의 메시지를 삭제합니다."
)
@app_commands.describe(
    개수="삭제할 메시지 개수 (1~100)"
)
@app_commands.checks.has_permissions(
    administrator=True
)
async def clear_messages(
    interaction: discord.Interaction,
    개수: int
):

    if 개수 < 1 or 개수 > 100:

        await interaction.response.send_message(
            "❌ 1개 이상 100개 이하의 "
            "개수를 입력해 주세요.",
            ephemeral=True
        )

        return

    await interaction.response.defer(
        ephemeral=True
    )

    deleted = await interaction.channel.purge(
        limit=개수
    )

    await interaction.followup.send(
        f"🧹 `{len(deleted)}`개의 메시지를 삭제했습니다.",
        ephemeral=True
    )


@clear_messages.error
async def clear_messages_error(
    interaction: discord.Interaction,
    error: app_commands.AppCommandError
):

    if isinstance(
        error,
        app_commands.MissingPermissions
    ):

        await interaction.response.send_message(
            "❌ 이 명령어를 사용할 권한(관리자)이 없습니다.",
            ephemeral=True
        )


# =========================================================
# 21. 안전한 실행
# =========================================================

if __name__ == "__main__":

    # -----------------------------------------------------
    # Flask 시작
    # -----------------------------------------------------

    keep_alive()

    time.sleep(1)

    # -----------------------------------------------------
    # Discord Token
    # -----------------------------------------------------

    ENV_VALUE = os.environ.get(
        "DISCORD_TOKEN"
    )

    if not ENV_VALUE:

        print(
            "CRITICAL ERROR: "
            "DISCORD_TOKEN 환경변수가 설정되지 않았습니다.",
            file=sys.stderr
        )

        sys.exit(1)

    # -----------------------------------------------------
    # TOKEN|LOG_CHANNEL_ID 방식
    # -----------------------------------------------------

    if "|" in ENV_VALUE:

        TOKEN, channel_id_str = ENV_VALUE.split(
            "|",
            1
        )

        try:

            LOG_CHANNEL_ID = int(
                channel_id_str.strip()
            )

        except ValueError:

            LOG_CHANNEL_ID = 0

    else:

        TOKEN = ENV_VALUE

        LOG_CHANNEL_ID = 0

    print(
        f"Loaded LOG_CHANNEL_ID: "
        f"{LOG_CHANNEL_ID}"
    )

    # -----------------------------------------------------
    # 봇 실행
    # -----------------------------------------------------

    try:

        bot.run(
            TOKEN.strip()
        )

    except Exception as e:

        print(
            f"CRITICAL ERROR: "
            f"Bot failed to run: {e}",
            file=sys.stderr
        )

        print(
            "Waiting 30 seconds before exiting "
            "to prevent Render rapid restart loops...",
            file=sys.stderr
        )

        time.sleep(30)

        sys.exit(1)
