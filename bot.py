import os
import json
import asyncio
from datetime import datetime, timedelta

import discord
from discord.ext import commands, tasks
from discord import app_commands

# ====== Botトークン（Railway環境変数） ======
TOKEN = os.getenv("TOKEN")

# ====== 設定ファイル ======
CONFIG_PATH = "vc_config.json"        # VC管理・ログ・ターゲット
DATA_FILE = "last_active.json"        # 最終参加日
ROLE_FILE = "active_role.json"        # アクティブロールID（ギルド別）
GAME_CONFIG_FILE = "game_config.json" # ゲームリスト＆通知チャンネル

intents = discord.Intents.all()
bot = commands.Bot(command_prefix="/", intents=intents)

vc_sessions = {}  # VCごとの通話セッション情報


# =========================
# 共通JSONユーティリティ
# =========================

def load_json(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return {}


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)


# =========================
# VC設定ロード
# =========================

def load_config():
    cfg = load_json(CONFIG_PATH)
    if "vc_targets" not in cfg:
        cfg["vc_targets"] = []
    if "log_channel_id" not in cfg:
        cfg["log_channel_id"] = None
    if "rename_targets" not in cfg:
        cfg["rename_targets"] = []
    if "game_targets" not in cfg:
        cfg["game_targets"] = []
    return cfg


def save_config(config):
    save_json(CONFIG_PATH, config)


config = load_config()


# =========================
# ゲーム設定ロード
# =========================

def load_game_config():
    cfg = load_json(GAME_CONFIG_FILE)
    if "game_list_channel_id" not in cfg:
        cfg["game_list_channel_id"] = None
    if "notice_channel_id" not in cfg:
        cfg["notice_channel_id"] = None
    return cfg


def save_game_config(cfg):
    save_json(GAME_CONFIG_FILE, cfg)


game_config = load_game_config()


# =========================
# 共通ヘルパー
# =========================

def human_count(channel: discord.VoiceChannel) -> int:
    return sum(1 for m in channel.members if not m.bot)


async def ensure_chat(guild: discord.Guild, chat_name: str, category: discord.CategoryChannel | None):
    for ch in guild.text_channels:
        if ch.name == chat_name:
            return ch
    return await guild.create_text_channel(chat_name, category=category)


async def delete_chat(guild: discord.Guild, chat_name: str):
    for ch in guild.text_channels:
        if ch.name == chat_name:
            await ch.delete()
            return


def get_vc_target(vc_id: int):
    for t in config["vc_targets"]:
        if t["vc_id"] == vc_id:
            return t
    return None


def get_log_channel(guild: discord.Guild) -> discord.TextChannel | None:
    log_id = config.get("log_channel_id")
    if not log_id:
        return None
    return guild.get_channel(log_id)


def ensure_session(vc_id: int):
    if vc_id not in vc_sessions:
        vc_sessions[vc_id] = {
            "start_time": None,
            "members": {}
        }
    return vc_sessions[vc_id]


def format_dt(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def extract_base_name(name: str):
    separators = ["｜", "|", " ", "-", "_"]
    for sep in separators:
        if sep in name:
            return name.split(sep)[0]
    return name


# =========================
# ゲームリスト読み取り
# =========================

async def load_games(bot: commands.Bot):
    ch_id = game_config.get("game_list_channel_id")
    if not ch_id:
        return []

    channel = bot.get_channel(ch_id)
    if not isinstance(channel, discord.TextChannel):
        return []

    messages = [message async for message in channel.history(limit=50)]
    games = [m.content.strip() for m in messages if m.content.strip()]
    return games


# =========================
# ゲーム選択ボタンUI
# =========================

class GameButton(discord.ui.Button):
    def __init__(self, title: str):
        super().__init__(label=title, style=discord.ButtonStyle.primary)
        self.title = title

    async def callback(self, interaction: discord.Interaction):
        await change_vc_name_by_game(interaction, self.title)


class GameSelect(discord.ui.View):
    def __init__(self, games: list[str]):
        super().__init__(timeout=None)
        for g in games:
            self.add_item(GameButton(g))


# =========================
# ゲーム選択によるVC名変更
# =========================

async def change_vc_name_by_game(interaction: discord.Interaction, title: str):
    user = interaction.user

    if not isinstance(user, discord.Member):
        await interaction.response.send_message("サーバー内のメンバーだけが使えるよ。", ephemeral=True)
        return

    if not user.voice or not user.voice.channel:
        await interaction.response.send_message("VCに入ってから押してね！", ephemeral=True)
        return

    vc = user.voice.channel

    if vc.id not in config.get("game_targets", []):
        await interaction.response.send_message("このVCではゲーム選択は使えないよ！", ephemeral=True)
        return

    base = extract_base_name(vc.name)
    await vc.edit(name=f"{base}｜{title}")

    await interaction.response.send_message(
        f"VC名を **{title}** に変更したよ！",
        delete_after=60
    )


# =========================
# ゲーム関連チャンネル設定コマンド
# =========================

@bot.tree.command(name="set_game_list_channel", description="ゲームリストチャンネルを設定する")
@app_commands.describe(channel_id="ゲームリストチャンネルのID")
async def set_game_list_channel(interaction: discord.Interaction, channel_id: str):
    ch_id_int = int(channel_id)
    channel = interaction.guild.get_channel(ch_id_int)

    if not isinstance(channel, discord.TextChannel):
        await interaction.response.send_message("そのIDのテキストチャンネルが見つからないよ。", ephemeral=True)
        return

    game_config["game_list_channel_id"] = ch_id_int
    save_game_config(game_config)

    await interaction.response.send_message(
        f"ゲームリストチャンネルを {channel.mention} に設定したよ！",
        ephemeral=True
    )


@bot.tree.command(name="set_notice_channel", description="ゲーム選択ボタンを送る通知チャンネルを設定する")
@app_commands.describe(channel_name="通知チャンネルの名前")
async def set_notice_channel(interaction: discord.Interaction, channel_name: str):
    channel = discord.utils.get(interaction.guild.text_channels, name=channel_name)

    if not channel or not isinstance(channel, discord.TextChannel):
        await interaction.response.send_message("その名前のテキストチャンネルが見つからないよ。", ephemeral=True)
        return

    game_config["notice_channel_id"] = channel.id
    save_game_config(game_config)

    await interaction.response.send_message(
        f"通知チャンネルを {channel.mention} に設定したよ！",
        ephemeral=True
    )


@bot.tree.command(name="game_channel_status", description="ゲーム関連チャンネル設定を確認する")
async def game_channel_status(interaction: discord.Interaction):
    gl = game_config.get("game_list_channel_id")
    nc = game_config.get("notice_channel_id")

    msg = "**ゲーム関連チャンネル設定**\n"
    msg += f"- ゲームリスト: {gl if gl else '未設定'}\n"
    msg += f"- 通知チャンネル: {nc if nc else '未設定'}\n"

    await interaction.response.send_message(msg, ephemeral=True)


# =========================
# VC管理コマンド（追加・削除・一覧）
# =========================

@bot.tree.command(name="vc_add", description="VC管理対象を追加する")
@app_commands.describe(vc_name="対象VCの名前", chat_name="生成するチャット名")
async def vc_add(interaction: discord.Interaction, vc_name: str, chat_name: str):
    vc = discord.utils.get(interaction.guild.voice_channels, name=vc_name)
    if not vc:
        await interaction.response.send_message("その名前のVCが見つかりません。", ephemeral=True)
        return

    if get_vc_target(vc.id):
        await interaction.response.send_message("そのVCはすでに登録されています。", ephemeral=True)
        return

    config["vc_targets"].append({
        "vc_id": vc.id,
        "chat_name": chat_name,
        "start_message": "通話開始！",
        "log_start_message_id": None
    })
    save_config(config)

    await interaction.response.send_message(
        f"VC「{vc.name}」を管理対象として追加しました。\nチャット名: {chat_name}",
        ephemeral=True
    )


@bot.tree.command(name="vc_remove", description="VC管理対象を削除する")
@app_commands.describe(vc_name="対象VCの名前")
async def vc_remove(interaction: discord.Interaction, vc_name: str):
    vc = discord.utils.get(interaction.guild.voice_channels, name=vc_name)
    if not vc:
        await interaction.response.send_message("その名前のVCが見つかりません。", ephemeral=True)
        return

    before_len = len(config["vc_targets"])
    config["vc_targets"] = [t for t in config["vc_targets"] if t["vc_id"] != vc.id]
    after_len = len(config["vc_targets"])
    save_config(config)

    if before_len == after_len:
        await interaction.response.send_message("そのVCは登録されていません。", ephemeral=True)
    else:
        await interaction.response.send_message(f"VC「{vc.name}」を管理対象から削除しました。", ephemeral=True)
# =========================
# VC管理対象一覧
# =========================

@bot.tree.command(name="vc_list", description="VC管理対象一覧を表示する")
async def vc_list(interaction: discord.Interaction):
    if not config["vc_targets"]:
        await interaction.response.send_message("管理対象のVCはありません。", ephemeral=True)
        return

    lines = []
    for t in config["vc_targets"]:
        lines.append(
            f"VC ID: {t['vc_id']} / chat_name: {t['chat_name']} / start_message: {t['start_message']}"
        )
    msg = "\n".join(lines)

    await interaction.response.send_message(f"管理対象VC一覧:\n{msg}", ephemeral=True)
# =========================
# VC開始メッセージ変更
# =========================

@bot.tree.command(name="vc_set_start_message", description="VCチャットに送る開始メッセージを変更する")
@app_commands.describe(vc_name="対象VCの名前", message="開始時にVCチャットへ送るメッセージ")
async def vc_set_start_message(interaction: discord.Interaction, vc_name: str, message: str):
    vc = discord.utils.get(interaction.guild.voice_channels, name=vc_name)
    if not vc:
        await interaction.response.send_message("その名前のVCが見つかりません。", ephemeral=True)
        return

    target = get_vc_target(vc.id)
    if not target:
        await interaction.response.send_message("そのVCは管理対象に登録されていません。", ephemeral=True)
        return

    target["start_message"] = message
    save_config(config)

    await interaction.response.send_message(
        f"VC「{vc.name}」の開始メッセージを変更しました:\n{message}",
        ephemeral=True
    )
# =========================
# VCログチャンネル設定
# =========================

@bot.tree.command(name="vc_set_log_channel", description="VCログを送るチャンネルを設定する")

@app_commands.describe(channel_id="ログチャンネルのID")
async def set_vc_log_channel(interaction: discord.Interaction, channel_id: str):
    ch_id_int = int(channel_id)
    channel = interaction.guild.get_channel(ch_id_int)

    if not isinstance(channel, discord.TextChannel):
        await interaction.response.send_message("そのIDのテキストチャンネルが見つかりません。", ephemeral=True)
        return

    config["log_channel_id"] = ch_id_int
    save_config(config)

    await interaction.response.send_message(
        f"ログチャンネルを {channel.mention} に設定しました。",
        ephemeral=True
    )
    
@bot.tree.command(name="vc_log_channel_status", description="現在のログチャンネル設定を確認する")
async def vc_log_channel_status(interaction: discord.Interaction):
    log_ch = get_log_channel(interaction.guild)

    if not log_ch:
        await interaction.response.send_message("ログチャンネルは未設定です。", ephemeral=True)
    else:
        await interaction.response.send_message(
            f"現在のログチャンネル: {log_ch.mention} (ID: {log_ch.id})",
            ephemeral=True
        )

# =========================
# VC名変更対象（複数VC対応）
# =========================

@bot.tree.command(name="vc_add_rename_target", description="VC名変更対象を追加する")
@app_commands.describe(vc_id="対象VCのID")
async def vc_add_rename_target(interaction: discord.Interaction, vc_id: str):
    vc_id_int = int(vc_id)

    if vc_id_int in config.get("rename_targets", []):
        await interaction.response.send_message("そのVCはすでに登録されています。", ephemeral=True)
        return

    config["rename_targets"].append(vc_id_int)
    save_config(config)

    await interaction.response.send_message(
        f"VC名変更対象として VC {vc_id_int} を追加しました。",
        ephemeral=True
    )


@bot.tree.command(name="vc_remove_rename_target", description="VC名変更対象を削除する")
@app_commands.describe(vc_id="対象VCのID")
async def vc_remove_rename_target(interaction: discord.Interaction, vc_id: str):
    vc_id_int = int(vc_id)

    if vc_id_int not in config.get("rename_targets", []):
        await interaction.response.send_message("そのVCは登録されていません。", ephemeral=True)
        return

    config["rename_targets"].remove(vc_id_int)
    save_config(config)

    await interaction.response.send_message(
        f"VC名変更対象から VC {vc_id_int} を削除しました。",
        ephemeral=True
    )


@bot.tree.command(name="vc_rename_targets", description="VC名変更対象一覧を表示する")
async def vc_rename_targets(interaction: discord.Interaction):
    targets = config.get("rename_targets", [])

    if not targets:
        await interaction.response.send_message("VC名変更対象はありません。", ephemeral=True)
        return

    lines = [f"- VC ID: {vc_id}" for vc_id in targets]
    msg = "\n".join(lines)

    await interaction.response.send_message(f"VC名変更対象一覧:\n{msg}", ephemeral=True)


@bot.tree.command(name="vc_rename", description="対象VCの名前を変更する")
@app_commands.describe(vc_id="対象VCのID", new_name="新しいVC名")
async def vc_rename(interaction: discord.Interaction, vc_id: str, new_name: str):
    vc_id_int = int(vc_id)

    if vc_id_int not in config.get("rename_targets", []):
        await interaction.response.send_message("そのVCはVC名変更対象に登録されていません。", ephemeral=True)
        return

    vc = interaction.guild.get_channel(vc_id_int)
    if not vc or not isinstance(vc, discord.VoiceChannel):
        await interaction.response.send_message("対象VCが見つかりません。", ephemeral=True)
        return

    await vc.edit(name=new_name)
    await interaction.response.send_message(f"VC名を変更しました：{new_name}", ephemeral=True)

    # VCチャット通知（管理対象VCのみ）
    target = get_vc_target(vc_id_int)
    if target:
        chat = await ensure_chat(interaction.guild, target["chat_name"], vc.category)
        await asyncio.sleep(2)
        await chat.send(f"VC名を **{new_name}** に変更しました。")


# =========================
# ゲーム選択対象VC（複数）
# =========================

@bot.tree.command(name="vc_add_game_target", description="ゲーム選択ボタン対象VCを追加する")
@app_commands.describe(vc_name="対象VCの名前")
async def vc_add_game_target(interaction: discord.Interaction, vc_name: str):
    vc = discord.utils.get(interaction.guild.voice_channels, name=vc_name)
    if not vc:
        await interaction.response.send_message("その名前のVCが見つかりません。", ephemeral=True)
        return

    if vc.id in config.get("game_targets", []):
        await interaction.response.send_message("そのVCはすでに登録されています。", ephemeral=True)
        return

    config["game_targets"].append(vc.id)
    save_config(config)

    await interaction.response.send_message(
        f"ゲーム選択ボタン対象として VC「{vc.name}」を追加しました。",
        ephemeral=True
    )


@bot.tree.command(name="vc_remove_game_target", description="ゲーム選択ボタン対象VCを削除する")
@app_commands.describe(vc_name="対象VCの名前")
async def vc_remove_game_target(interaction: discord.Interaction, vc_name: str):
    vc = discord.utils.get(interaction.guild.voice_channels, name=vc_name)
    if not vc:
        await interaction.response.send_message("その名前のVCが見つかりません。", ephemeral=True)
        return

    if vc.id not in config.get("game_targets", []):
        await interaction.response.send_message("そのVCは登録されていません。", ephemeral=True)
        return

    config["game_targets"].remove(vc.id)
    save_config(config)

    await interaction.response.send_message(
        f"ゲーム選択ボタン対象から VC「{vc.name}」を削除しました。",
        ephemeral=True
    )


@bot.tree.command(name="vc_game_targets", description="ゲーム選択ボタン対象VC一覧を表示する")
async def vc_game_targets(interaction: discord.Interaction):
    targets = config.get("game_targets", [])

    if not targets:
        await interaction.response.send_message("ゲーム選択ボタン対象VCはありません。", ephemeral=True)
        return

    lines = [f"- VC ID: {vc_id}" for vc_id in targets]
    msg = "\n".join(lines)

    await interaction.response.send_message(f"ゲーム選択ボタン対象VC一覧:\n{msg}", ephemeral=True)


# =========================
# VC入退室イベント
# =========================

@bot.event
async def on_voice_state_update(member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
    guild = member.guild

    # =========================
    # ① 30日アクティブロール：VC参加時
    # =========================
    if after.channel:
        active_data = load_json(DATA_FILE)
        today = datetime.now().strftime("%Y-%m-%d")
        active_data[str(member.id)] = today
        save_json(DATA_FILE, active_data)

        role_data = load_json(ROLE_FILE)
        guild_role_info = role_data.get(str(guild.id))
        if guild_role_info:
            role_id = guild_role_info.get("role_id")
            role = guild.get_role(role_id)
            if role and role not in member.roles:
                await member.add_roles(role)
                print(f"{member.display_name} にアクティブロールを付与した")

    # =========================
    # ② VC管理：入室処理
    # =========================
    if after.channel:
        target = get_vc_target(after.channel.id)
        if target:
            session = ensure_session(after.channel.id)

            # VCチャット生成（人間1人目のときだけ）
            if human_count(after.channel) == 1:
                chat = discord.utils.get(guild.text_channels, name=target["chat_name"])
                if not chat:
                    chat = await guild.create_text_channel(
                        target["chat_name"],
                        category=after.channel.category
                    )
            else:
                chat = await ensure_chat(
                    guild,
                    target["chat_name"],
                    after.channel.category
                )

            join_time = datetime.now()
            await chat.send(f"{member.mention} が参加しました ({format_dt(join_time)})")

            # セッション記録
            if member.id not in session["members"]:
                session["members"][member.id] = {"join": join_time, "leave": None}
            else:
                session["members"][member.id]["join"] = join_time
                session["members"][member.id]["leave"] = None

            # VC開始（人間1人目）
            if human_count(after.channel) == 1:
                session["start_time"] = join_time

                start_msg = target.get("start_message", "通話開始！")
                await chat.send(start_msg)

                # ログチャンネルへ開始ログ
                log_ch = get_log_channel(guild)
                if log_ch:
                    msg = await log_ch.send(
                        f"[VC開始] VC: {after.channel.name} (ID: {after.channel.id})\n"
                        f"開始時刻: {format_dt(join_time)}"
                    )
                    target["log_start_message_id"] = msg.id
                    save_config(config)

        # =========================
        # ③ ゲーム選択ボタン：対象VC入室時
        # =========================
        if after.channel.id in config.get("game_targets", []):
            if not before.channel or before.channel.id != after.channel.id:
                games = await load_games(bot)

                target = get_vc_target(after.channel.id)
                if target:
                    chat = discord.utils.get(guild.text_channels, name=target["chat_name"])
                    if not chat:
                        chat = await guild.create_text_channel(
                            target["chat_name"],
                            category=after.channel.category
                        )

                    await asyncio.sleep(2)
                    await chat.send(
                        f"{member.display_name} がVCに入りました。ゲームを選択してね👇",
                        view=GameSelect(games)
                    )
        # ================================
        # ④ VC管理：退出処理（遅延 + 自前カウント方式）
        # ================================
        if before.channel and after.channel is None:
            target = get_vc_target(before.channel.id)
            if target:
                session = ensure_session(before.channel.id)

                # 自前カウント（退出時は -1）
                if "count" not in session:
                    session["count"] = 0
                session["count"] -= 1

                # 遅延してから終了判定（Discord遅延対策）
                await asyncio.sleep(1)

                # VC終了判定（自前カウント + Discordのmembers両方見る）
                is_empty = session["count"] <= 0 or len(before.channel.members) == 0

                if is_empty:
                    chat = await ensure_chat(guild, target["chat_name"], before.channel.category)

                    leave_time = datetime.now()
                    await chat.send(f"{member.mention} が退出しました（{format_dt(leave_time)}）")

                    # セッション記録
                    if member.id in session["members"]:
                        session["members"][member.id]["leave"] = leave_time
                    else:
                        session["members"][member.id] = {"join": None, "leave": leave_time}

                    start_time = session.get("start_time")
                    end_time = leave_time

                    # ログチャンネルへ終了ログ
                    log_ch = get_log_channel(guild)
                    if log_ch and start_time:
                        lines = []
                        for uid, times in session["members"].items():
                            user = guild.get_member(uid)
                            name = user.display_name if user else f"ID:{uid}"
                            j = times["join"]
                            l = times["leave"] or end_time
                            lines.append(f"{name}: {format_dt(j) if j else '不明'} ～ {format_dt(l)}")

                        participants = "\n".join(lines) if lines else "参加者情報なし"

                        await log_ch.send(
                            f"[VC終了] VC: {before.channel.name} (ID: {before.channel.id})\n"
                            f"開始: {format_dt(start_time)}\n"
                            f"終了: {format_dt(end_time)}\n"
                            f"参加者:\n{participants}"
                        )

                        # 開始ログ削除
                        start_msg_id = target.get("log_start_message_id")
                        if start_msg_id:
                            try:
                                start_msg = await log_ch.fetch_message(start_msg_id)
                                await start_msg.delete()
                            except Exception:
                                pass
                            target["log_start_message_id"] = None
                            save_config(config)

                    # VCチャット削除
                    await asyncio.sleep(1)
                    await delete_chat(guild, target["chat_name"])

                    # セッションリセット
                    vc_sessions[before.channel.id] = {
                        "start_time": None,
                        "members": {},
                        "count": 0
                    }

            # ================================
            # ⑤ ゲーム選択ボタン：退出時はベース名に戻す
            # ================================
            if before.channel.id in config.get("game_targets", []):
                # 遅延してから確認（Discord遅延対策）
                await asyncio.sleep(1)
                if human_count(before.channel) == 0:
                    vc = before.channel
                    base = extract_base_name(vc.name)
                    if vc.name != base:
                        await vc.edit(name=base)

# =========================
# 30日アクティブロールチェック（毎日1回）
# =========================

@tasks.loop(hours=24)
async def check_inactive_users():
    role_data = load_json(ROLE_FILE)
    active_data = load_json(DATA_FILE)
    now = datetime.now()

    for guild in bot.guilds:
        guild_role_info = role_data.get(str(guild.id))
        if not guild_role_info:
            continue

        role_id = guild_role_info.get("role_id")
        role = guild.get_role(role_id)
        if not role:
            continue

        for member in guild.members:
            if member.bot:
                continue

            last = active_data.get(str(member.id))
            if not last:
                continue

            last_date = datetime.strptime(last, "%Y-%m-%d")

            # 30日以上VC参加なし
            if now - last_date > timedelta(days=30):
                if role in member.roles:
                    await member.remove_roles(role)
                    print(f"{member.display_name} からアクティブロールを剥奪した")


# =========================
# Bot起動
# =========================

@bot.event
async def on_ready():
    print(f"Bot起動完了: {bot.user}")
    await bot.tree.sync()

    # 30日ロールチェック開始
    check_inactive_users.start()


# =========================
# Bot実行
# =========================

bot.run(TOKEN)

