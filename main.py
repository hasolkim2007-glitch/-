import os
import discord
from discord.ext import commands
from discord import app_commands
import datetime
from flask import flask
import asyncio

# ==========================================
# 1. Render 웹 바인딩용 Flask 서버 설정
# ==========================================
app = Flask(__name__)

@app.route('/')
def home():
    return "Bot is alive!"

def run_flask():
    # Render에서 지정하는 PORT 번호를 읽어옴 (기본값 10000)
    port = int(os.environ.get("PORT", 10000))
    # 0.0.0.0 포트로 바인딩
    app.run(host='0.0.0.0', port=port)
    
# --- 사용자 설정 영역 ---
TOKEN = os.environ.get('TOKEN')       # 디스코드 봇 토큰 입력
LOG_CHANNEL_ID = 1549301300053811290  # 운영진 로그 채널 ID (정수형)

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

current_view = None

class FirstComeLineView(discord.ui.View):
    def __init__(self, disabled_initial=True):
        super().__init__(timeout=None)
        self.is_closed = False
        self.clicked_user = None
        self.message = None  # 메시지 객체 저장용
        
        # 카운트다운 중에는 버튼을 누를 수 없도록 초기값을 disabled 상태로 설정
        if disabled_initial:
            for child in self.children:
                child.disabled = True

    # 모든 버튼의 활성화/비활성화 상태를 한 번에 전환하는 함수
    def set_all_buttons_disabled(self, disabled_state: bool):
        for child in self.children:
            child.disabled = disabled_state

    async def handle_selection(self, interaction: discord.Interaction, selection_name: str):
        # 1등 이후 들어온 클릭 차단 및 타임아웃 마감 차단
        if self.is_closed:
            await interaction.response.send_message(
                "❌ **이미 리롤이 마감되었습니다!**",
                ephemeral=True
            )
            return

        # 1등 접수 처리 및 즉시 버튼 잠금
        self.is_closed = True
        self.clicked_user = interaction.user
        self.set_all_buttons_disabled(True)
        await interaction.message.edit(view=self)

        # 제출 유저 확인 메시지
        await interaction.response.send_message(
            f" **리롤 [ {selection_name} ] 신청에 성공하셨습니다.**",
            ephemeral=True
        )

        # 패널 안내 문구 업데이트 (신청 완료 상태 표시)
        if self.message:
            embed = self.message.embeds[0]
            embed.title = "✅ 선착순 신청 마감"
            embed.description = f" ** [{selection_name}] 신청**"
            embed.color = discord.Color.blue()
            await self.message.edit(embed=embed, view=self)

        # 운영진 로그 채널 전송
        log_channel = interaction.guild.get_channel(LOG_CHANNEL_ID)
        if log_channel:
            now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
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

    # --- 1 ~ 4 라인 버튼 (1행) ---
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

    # --- 팀폭 버튼 (2행) ---
    @discord.ui.button(label="라인별팀폭", style=discord.ButtonStyle.danger, custom_id="team_bomb_line", row=1)
    async def line_bomb_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.handle_selection(interaction, "라인별팀폭")

    @discord.ui.button(label="올랜팀폭", style=discord.ButtonStyle.danger, custom_id="team_bomb_allrand", row=1)
    async def allrand_bomb_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.handle_selection(interaction, "올랜팀폭")

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user.name}")
    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} command(s)")
    except Exception as e:
        print(e)

# 카운트다운 및 10초 대기 처리 함수
async def run_countdown_and_start(interaction: discord.Interaction, title_text: str):
    global current_view
    current_view = FirstComeLineView(disabled_initial=True)

    embed = discord.Embed(
        title=f"⏳ {title_text}",
        description="**카운트다운 진행 중... 잠시만 기다려주세요!**\n\n3️⃣",
        color=discord.Color.yellow()
    )
    
    # 1. 비활성화된 패널 메시지 생성
    msg = await interaction.channel.send(embed=embed, view=current_view)
    current_view.message = msg
    await interaction.response.send_message("카운트다운을 시작합니다.", ephemeral=True)

    # 2. 시작 카운트다운 (3초)
    await asyncio.sleep(1)
    embed.description = "**카운트다운 진행 중... 잠시만 기다려주세요!**\n\n2️⃣"
    await msg.edit(embed=embed)

    await asyncio.sleep(1)
    embed.description = "**카운트다운 진행 중... 잠시만 기다려주세요!**\n\n1️⃣"
    await msg.edit(embed=embed)

    await asyncio.sleep(1)
    
    # 3. 버튼 잠금 해제 및 10초 타이머 시작
    current_view.set_all_buttons_disabled(False)
    embed.title = f"⚡ {title_text}"
    embed.description = "🔥 **신청 시작!! (남은 시간: 10초)**"
    embed.color = discord.Color.green()
    await msg.edit(embed=embed, view=current_view)

    # 4. 10초 제한시간 타이머 루프
    for remaining in range(9, -1, -1):
        await asyncio.sleep(1)
        # 이미 누가 클릭해서 마감되었다면 타이머 종료
        if current_view.is_closed:
            return
        
        if remaining > 0:
            embed.description = f"🔥 **신청 시작!! (남은 시간: {remaining}초)**"
            await msg.edit(embed=embed)

    # 5. 10초 동안 아무도 안 눌렀을 경우 자동 마감 처리
    if not current_view.is_closed:
        current_view.is_closed = True
        current_view.set_all_buttons_disabled(True)
        
        embed.title = f"⏰ {title_text} (마감)"
        embed.description = "⏱️ **10초 동안 신청이 없어 자동으로 마감되었습니다.**"
        embed.color = discord.Color.dark_gray()
        await msg.edit(embed=embed, view=current_view)

# 1. 첫 패널 생성 명령어 (/시작)
@bot.tree.command(name="시작", description="카운트다운 후 선착순 신청 버튼을 오픈합니다. (10초 제한)")
@app_commands.checks.has_permissions(administrator=True)
async def create_panel(interaction: discord.Interaction):
    await run_countdown_and_start(interaction, "리롤 / 팀폭 신청")

# 2. 다음 라운드 진행 명령어 (/리셋)
@bot.tree.command(name="리셋", description="카운트다운 후 새 라운드 신청창을 생성합니다. (10초 제한)")
@app_commands.checks.has_permissions(administrator=True)
async def reset_panel(interaction: discord.Interaction):
    await run_countdown_and_start(interaction, "리롤 / 팀폭 신청")

# 메시지 일괄 삭제 명령어 (/청소 개수)
@bot.tree.command(name="청소", description="채널의 메시지를 일괄 삭제합니다. (1~100개)")
@app_commands.checks.has_permissions(administrator=True)
async def clear_messages(interaction: discord.Interaction, amount: int):
    if amount < 1 or amount > 100:
        await interaction.response.send_message("❌ 1개 이상 100개 이하의 숫자를 입력해주세요.", ephemeral=True)
        return

    # 응답 대기 처리 (Ephemeral 메시지로 전송하여 퍼지 대상에서 제외)
    await interaction.response.send_message(f"🧹 최근 메시지 {amount}개를 삭제하는 중입니다...", ephemeral=True)
    
    # 메시지 일괄 삭제 실행
    deleted = await interaction.channel.purge(limit=amount)
    
    # 완료 메시지 전송
    await interaction.followup.send(f"✅ 총 **{len(deleted)}개**의 메시지를 깔끔하게 삭제했습니다!", ephemeral=True)

# 관리자 권한 예외 처리
@clear_messages.error
async def clear_messages_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.MissingPermissions):
        await interaction.response.send_message("❌ 이 명령어를 사용할 권한(관리자)이 없습니다.", ephemeral=True)

# ==========================================
# 인원 체크 (출석 패널) View 클래스
# ==========================================
class AttendanceView(discord.ui.View):
    def __init__(self, title: str, start_time_obj: datetime.datetime, raw_time_str: str):
        super().__init__(timeout=None)
        self.title = title
        self.start_time_obj = start_time_obj
        self.raw_time_str = raw_time_str
        self.participants = set()   # 참가자 목록 (User 객체)

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

    # [참가 버튼]
    @discord.ui.button(label="참가", style=discord.ButtonStyle.success, custom_id="attend_yes", row=0)
    async def yes_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        user = interaction.user

        if user in self.participants:
            await interaction.response.send_message("이미 참가 명단에 등록되어 있습니다.", ephemeral=True)
            return

        self.participants.add(user)
        await interaction.response.edit_message(embed=self.build_embed(), view=self)
        await interaction.followup.send("✅ **참가 신청이 완료되었습니다.**", ephemeral=True)

    # [참가 취소 버튼]
    @discord.ui.button(label="참가 취소", style=discord.ButtonStyle.danger, custom_id="attend_cancel", row=0)
    async def cancel_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        user = interaction.user

        if user not in self.participants:
            await interaction.response.send_message("참가 명단에 등록되어 있지 않습니다.", ephemeral=True)
            return

        # --- 시작 20분 전 시간 제한 체크 ---
        if self.start_time_obj:
            now = datetime.datetime.now()
            cutoff_time = self.start_time_obj - datetime.timedelta(minutes=20)

            if now >= cutoff_time:
                await interaction.response.send_message(
                    f"❌ **참가 취소 불가!**\n시작 시간 20분 전({cutoff_time.strftime('%H:%M')})이 지나 더 이상 취소할 수 없습니다.",
                    ephemeral=True
                )
                return

        self.participants.remove(user)
        await interaction.response.edit_message(embed=self.build_embed(), view=self)
        await interaction.followup.send("❌ **참가가 취소되었습니다.**", ephemeral=True)


@bot.event
async def on_ready():
    print(f"Logged in as {bot.user.name}")
    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} command(s)")
    except Exception as e:
        print(e)


# ==========================================
# 슬래시 명령어 정의 (/인원체크)
# ==========================================
@bot.tree.command(name="인원체크", description="참가/취소 인원 체크 패널을 생성합니다. (20분 전 취소 제한)")
@app_commands.describe(제목="예: 오늘 내전 참가자 모집", 시작시간="HH:MM 형식 입력 (예: 21:00 또는 21:30)")
@app_commands.checks.has_permissions(administrator=True)
async def attendance_panel(interaction: discord.Interaction, 제목: str, 시작시간: str):
    start_time_obj = None
    try:
        # 입력받은 HH:MM을 오늘 날짜 기준 datetime으로 변환
        parsed_time = datetime.datetime.strptime(시작시간.strip(), "%H:%M").time()
        now = datetime.datetime.now()
        start_time_obj = datetime.datetime.combine(now.date(), parsed_time)
    except ValueError:
        await interaction.response.send_message(
            "❌ **시간 형식이 올바르지 않습니다.**\n`21:00` 또는 `09:30`처럼 **HH:MM** 형식으로 입력해 주세요.",
            ephemeral=True
        )
        return

    view = AttendanceView(title=제목, start_time_obj=start_time_obj, raw_time_str=시작시간)
    embed = view.build_embed()
    
    await interaction.response.send_message("인원 체크 패널이 생성되었습니다.", ephemeral=True)
    await interaction.channel.send(embed=embed, view=view)

bot.run(TOKEN)
