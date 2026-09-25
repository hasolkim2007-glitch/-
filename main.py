import os
import asyncio
import threading
from flask import Flask
from waitress import serve
import discord
from discord.ext import commands

# --- 1. Render 24시간 유지용 Flask 웹서버 ---
app = Flask('')

@app.route('/')
def home():
    return "Bot is alive!"

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    serve(app, host='0.0.0.0', port=port)

def keep_alive():
    t = threading.Thread(target=run_flask)
    t.daemon = True
    t.start()

# --- 2. 디스코드 봇 설정 ---
intents = discord.Intents.default()
intents.message_content = True  # 필요 시 활성화

bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user.name} (ID: {bot.user.id})")

# --- 3. 메인 비동기 실행 함수 ---
async def main():
    # 웹서버 실행
    keep_alive()
    
    # 디스코드 토큰 가져오기 (Render Environment Variable 권장)
    token = os.getenv("DISCORD_TOKEN") or "여기에_봇_토큰_입력"
    
    async with bot:
        await bot.start(token)

if __name__ == "__main__":
    asyncio.run(main())


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

        if self.is_closed:

            await interaction.response.send_message(
                "❌ **이미 리롤 신청이 마감되었습니다!**",
                ephemeral=True
            )

            return

        self.is_closed = True
        self.clicked_user = interaction.user

        self.set_all_buttons_disabled(True)

        user_id = interaction.user.id

        if user_id not in roll_stats:
            roll_stats[user_id] = get_empty_stats()

        roll_stats[user_id][selection_name] += 1

        embed = discord.Embed(
            title="✅ 리롤 / 팀폭 신청 마감",
            description=(
                f"🔥 **[{selection_name}] "
                f"신청 성공!**"
            ),
            color=discord.Color.green()
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

        await send_admin_log(
            interaction,
            "🎲 리롤 / 팀폭 신청 성공",
            selection_name,
            discord.Color.gold()
        )

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

    @discord.ui.button(
        label="라인별팀폭",
        style=discord.ButtonStyle.danger,
        custom_id="roll_bomb_line",
        row=1
    )
    async def roll_bomb_line(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button
    ):

        await self.handle_selection(
            interaction,
            "라인별팀폭"
        )

    @discord.ui.button(
        label="머리제외 올랜팀폭",
        style=discord.ButtonStyle.danger,
        custom_id="roll_bomb_all_no_head",
        row=1
    )
    async def roll_bomb_all_no_head(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button
    ):

        await self.handle_selection(
            interaction,
            "머리제외 올랜팀폭"
        )

    @discord.ui.button(
        label="머리포함 올랜팀폭",
        style=discord.ButtonStyle.danger,
        custom_id="roll_bomb_all_with_head",
        row=2
    )
    async def roll_bomb_all_with_head(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button
    ):

        await self.handle_selection(
            interaction,
            "머리포함 올랜팀폭"
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

        if self.is_closed:

            await interaction.response.send_message(
                "❌ **이미 팀폭 신청이 마감되었습니다!**",
                ephemeral=True
            )

            return

        self.is_closed = True
        self.clicked_user = interaction.user

        self.set_all_buttons_disabled(True)

        user_id = interaction.user.id

        if user_id not in roll_stats:
            roll_stats[user_id] = get_empty_stats()

        roll_stats[user_id][selection_name] += 1

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

        await send_admin_log(
            interaction,
            "💥 팀폭 신청 성공",
            selection_name,
            discord.Color.red()
        )

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

    await asyncio.sleep(1)

    embed.description = (
        "**카운트다운 진행 중... "
        "잠시만 기다려주세요!**\n\n"
        "2️⃣"
    )

    await msg.edit(
        embed=embed
    )

    await asyncio.sleep(1)

    embed.description = (
        "**카운트다운 진행 중... "
        "잠시만 기다려주세요!**\n\n"
        "1️⃣"
    )

    await msg.edit(
        embed=embed
    )

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
        f"⏱️ **마감까지 **"
    )

    embed.color = discord.Color.green()

    await msg.edit(
        embed=embed,
        view=current_view
    )

    start_wait = time.time()

    while (
        time.time() - start_wait
        < duration
    ):

        if current_view.is_closed:
            return

        await asyncio.sleep(0.5)

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

    await interaction.followup.send(
        (
            f"✅ **리롤/팀폭 결과 DM 전송 완료**\n\n"
            f"📨 DM 성공: `{dm_success}명`\n"
            f"❌ DM 실패: `{dm_fail}명`"
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
# 16. /초기화 (신청 기록 데이터 리셋)
# =========================================================

@bot.tree.command(
    name="초기화",
    description="누적된 리롤/팀폭 신청 데이터를 완전히 초기화합니다."
)
@app_commands.checks.has_permissions(
    administrator=True
)
async def reset_stats(
    interaction: discord.Interaction
):

    global roll_stats

    roll_stats.clear()

    await interaction.response.send_message(
        "🧹 **모든 리롤 / 팀폭 신청 데이터가 초기화되었습니다.**",
        ephemeral=True
    )


@reset_stats.error
async def reset_stats_error(
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
# 17. 메인 실행부
# =========================================================

if __name__ == "__main__":

    keep_alive()

    token = os.environ.get("DISCORD_TOKEN")

    if not token:
        print("[Error] DISCORD_TOKEN 환경 변수가 설정되지 않았습니다.")
        sys.exit(1)

import asyncio
from discord.errors import HTTPException

async def run_bot_with_retry():
    # 환경변수나 상수로 설정된 TOKEN / token 확인
    bot_token = globals().get('token') or globals().get('TOKEN') or os.environ.get('TOKEN')

    while True:
        try:
            await bot.start(bot_token)
            break
        except HTTPException as e:
            if e.status == 429:
                print("\n[429 Rate Limit] Discord IP 차단 감지됨. 5분 대기 후 재연결 시도...\n")
            else:
                print(f"[HTTP 에러 발생] {e}")
        except Exception as e:
            print(f"[Error] 예외 발생: {e}")
        finally:
            if not bot.is_closed():
                await bot.close()
            await asyncio.sleep(300)

if __name__ == "__main__":
    asyncio.run(run_bot_with_retry())
