import os
import discord
from discord.ext import commands
from discord import app_commands
import datetime
from flask import Flask
import asyncio
import threading

# ==========================================
# 1. Render 웹 바인딩용 Flask 서버 설정
# ==========================================
app = Flask(__name__)

@app.route('/')
def home():
    return "Bot is alive!"

def run_flask():
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)

# 백그라운드에서 Flask 실행
threading.Thread(target=run_flask, daemon=True).start()

# --- 사용자 설정 영역 ---
TOKEN = os.environ.get('TOKEN')
LOG_CHANNEL_ID = 1549301300053811290  # 운영진 로그 채널 ID

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

current_view = None
KST = datetime.timezone(datetime.timedelta(hours=9))


# ==========================================
# 선착순 패널 View 클래스
# ==========================================
class FirstComeLineView(discord.ui.View):
    def __init__(self, disabled_initial=True):
        super().__init__(timeout=None)
        self.is_closed = False
        self.clicked_user = None
        self.message = None

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

        self.is_closed = True
        self.clicked_user = interaction.user
        self.set_all_buttons_disabled(True)
        await interaction.message.edit(view=self)

        await interaction.response.send_message(
            f" **리롤 [ {selection_name} ] 신청에 성공하셨습니다.**",
            ephemeral=True
        )

        if self.message:
            embed = self.message.embeds[0]
            embed.title = "✅ 선착순 신청 마감"
            embed.description = f" ** [{selection_name}] 신청**"
            embed.color = discord.Color.blue()
            await self.message.edit(embed=embed, view=self)

        log_channel = interaction.guild.get_channel(LOG_CHANNEL_ID)
        if log_channel:
            now = datetime.datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
            user = interaction.user
            
            embed_log = discord.Embed(
                title="리롤/팀폭 신청 ",
                color=discord.Color.gold(),
                timestamp=datetime.datetime.now(datetime.timezone.utc)
            )
            embed_log.add_field(name="신청 항목", value=f"**{selection_name}**", inline=True)
            embed_log.add_field(name="당첨자", value=f"{user.mention} (`{user.display_name}`)", inline=True)
            embed_log.add_field(name="유저 ID", value=f"`{user.id}`", inline=False)
            embed_log.add_field(name="접수 시각", value=f"`{now}`", inline=False)

            await log_channel.send(embed=embed_log)

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


# ==========================================
# 인원 체크 (출석 패널) View 클래스
# ==========================================
class AttendanceView(discord.ui.View):
    def __init__(self, title: str, start_time_obj: datetime.datetime, raw_time_str: str, message: discord.Message = None):
        super().__init__(timeout=None)
        self.title = title
        self.start_time_obj = start_time_obj
        self.raw_time_str = raw_time_str
        self.participants = set()
        self.message = message

        # 생성 시점 이미 마감 기한이 지났는지 확인
        now = datetime.datetime.now(KST)
        cutoff_time = self.start_time_obj - datetime.timedelta(minutes=20) if self.start_time_obj else None
        
        if cutoff_time and now >= cutoff_time:
            self.disable_cancel_button()
        elif cutoff_time:
            # 20분 전 비활성화 타이머 예약 실행
            asyncio.create_task(self.schedule_lock())

    def disable_cancel_button(self):
        """참가 취소 버튼을 비활성화하는 내부 메쏘드"""
        for child in self.children:
            if isinstance(child, discord.ui.Button) and child.custom_id == "attend_cancel":
                child.disabled = True
                child.label = "취소 마감"

    async def schedule_lock(self):
        """20분 전 마감 시점에 맞춰 취소 버튼을 비활성화함"""
        now = datetime.datetime.now(KST)
        cutoff_time = self.start_time_obj - datetime.timedelta(minutes=20)
        wait_seconds = (cutoff_time - now).total_seconds()

        if wait_seconds > 0:
            await asyncio.sleep(wait_seconds)

        # 마감 처리 및 패널 메시지 업데이트
        self.disable_cancel_button()
        if self.message:
            try:
                await self.message.edit(embed=self.build_embed(), view=self)
            except Exception as e:
                print(f"Error updating lock view: {e}")

    def build_embed(self) -> discord.Embed:
        cutoff_time_str = (self.start_time_obj - datetime.timedelta(minutes=20)).strftime("%H:%M") if self.start_time_obj else "시작 20분 전"
        
        embed = discord.Embed(
            title=f"📋 {self.title}",
            description=f"⏰ **시작 시간:** {self.raw_time_str}\n⚠️ **취소 가능 기한:** {cutoff_time_str}까지 (시작 20분 전)",
            color=discord.Color.green()
        )

        part_text = "\n".join([f"• {u.mention}" for u in self.participants]) if self.participants else "없음"
        embed.add_field(name=f"✅ 참가 명단 ({len(self.participants)}명)", value=part_text, inline=False)
        embed.set_footer(text="취소는 시작 시간 20분 전까지만 가능합니다.")
        return embed

    @discord.ui.button(label="참가", style=discord.ButtonStyle.success, custom_id="attend_yes", row=0)
    async def yes_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        user = interaction.user

        if user in self.participants:
            await interaction.response.send_message("이미 참가 명단에 등록되어 있습니다.", ephemeral=True)
            return

        self.participants.add(user)
        await interaction.response.edit_message(embed=self.build_embed(), view=self)
        await interaction.followup.send("✅ **참가 신청이 완료되었습니다.**", ephemeral=True)

    @discord.ui.button(label="참가 취소", style=discord.ButtonStyle.danger, custom_id="attend_cancel", row=0)
    async def cancel_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        user = interaction.user

        if user not in self.participants:
            await interaction.response.send_message("참가 명단에 등록되어 있지 않습니다.", ephemeral=True)
            return

        # 백업 검증 로직
        if self.start_time_obj:
            now = datetime.datetime.now(KST)
            cutoff_time = self.start_time_obj - datetime.timedelta(minutes=20)

            if now >= cutoff_time:
                self.disable_cancel_button()
                await interaction.response.edit_message(embed=self.build_embed(), view=self)
                await interaction.followup.send("❌ **시작 20분 전이 지나 참가 취소가 마감되었습니다.**", ephemeral=True)
                return

        self.participants.remove(user)
        await interaction.response.edit_message(embed=self.build_embed(), view=self)
        await interaction.followup.send("❌ **참가가 취소되었습니다.**", ephemeral=True)


# ==========================================
# 카운트다운 로직
# ==========================================
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


# ==========================================
# 이벤트 및 슬래시 명령어 정의
# ==========================================
@bot.event
async def on_ready():
    print(f"Logged in as {bot.user.name}")
    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} command(s)")
    except Exception as e:
        print(e)


@bot.tree.command(name="시작", description="카운트다운 후 선착순 신청 버튼을 오픈합니다. (10초 제한)")
@app_commands.checks.has_permissions(administrator=True)
async def create_panel(interaction: discord.Interaction):
    await run_countdown_and_start(interaction, "리롤 / 팀폭 신청")


@bot.tree.command(name="리셋", description="카운트다운 후 새 라운드 신청창을 생성합니다. (10초 제한)")
@app_commands.checks.has_permissions(administrator=True)
async def reset_panel(interaction: discord.Interaction):
    await run_countdown_and_start(interaction, "리롤 / 팀폭 신청")


@bot.tree.command(name="청소", description="채널의 메시지를 일괄 삭제합니다. (1~100개)")
@app_commands.checks.has_permissions(administrator=True)
async def clear_messages(interaction: discord.Interaction, amount: int):
    if amount < 1 or amount > 100:
        await interaction.response.send_message("❌ 1개 이상 100개 이하의 숫자를 입력해주세요.", ephemeral=True)
        return

    await interaction.response.send_message(f"🧹 최근 메시지 {amount}개를 삭제하는 중입니다...", ephemeral=True)
    deleted = await interaction.channel.purge(limit=amount)
    await interaction.followup.send(f"✅ 총 **{len(deleted)}개**의 메시지를 깔끔하게 삭제했습니다!", ephemeral=True)


@clear_messages.error
async def clear_messages_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.MissingPermissions):
        await interaction.response.send_message("❌ 이 명령어를 사용할 권한(관리자)이 없습니다.", ephemeral=True)


@bot.tree.command(name="인원체크", description="참가/취소 인원 체크 패널을 생성합니다. (20분 전 취소 제한)")
@app_commands.describe(제목="예: 오늘 내전 참가자 모집", 시작시간="HH:MM 형식 입력 (예: 21:00 또는 21:30)")
@app_commands.checks.has_permissions(administrator=True)
async def attendance_panel(interaction: discord.Interaction, 제목: str, 시작시간: str):
    start_time_obj = None
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
    
    # 생성된 메시지 객체를 View에 연결해 타이머 작동 시 메시지를 자동 수정할 수 있게 설정
    view.message = sent_msg


if __name__ == "__main__":
    if TOKEN:
        bot.run(TOKEN)
    else:
        print("ERROR: TOKEN 환경변수가 설정되지 않았습니다.")
