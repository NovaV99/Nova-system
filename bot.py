from __future__ import annotations

import discord
from discord.ext import commands, tasks
from discord.ui import Button, View, Select
import json
import os
import re
import asyncio
import random
from datetime import datetime, timedelta
import yt_dlp as youtube_dl


intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.voice_states = True
intents.guilds = True
bot = commands.Bot(command_prefix='+', intents=intents)
bot.remove_command('help')


def load_json(file):
    if not os.path.exists(file):
        return {}
    with open(file, 'r') as f:
        return json.load(f)

def save_json(file, data):
    with open(file, 'w') as f:
        json.dump(data, f, indent=4)


warnings = load_json('warnings.json')
filter_words = load_json('filter_words.json')
giveaways = load_json('giveaways.json')
autorole_data = load_json('autorole.json')
welcome_data = load_json('welcome.json')
goodbye_data = load_json('goodbye.json')
log_channels = load_json('log_channels.json')
config = load_json('config.json')
tickets = load_json('tickets.json')
whitelist_data = load_json('whitelist.json')
level_data = load_json('levels.json')
level_roles = load_json('level_roles.json')
queues = {}

if 'automod' not in config:
    config['automod'] = {
        'caps_limit': 70,
        'caps_action': 'warn',
        'mention_limit': 5,
        'mention_action': 'warn',
        'invite_action': 'delete',
        'spam_count': 5,
        'spam_action': 'timeout'
    }
    save_json('config.json', config)

user_messages = {}


XP_PER_MESSAGE = (10, 20)

def add_xp(guild_id, user_id, xp):
    gid = str(guild_id)
    uid = str(user_id)
    if gid not in level_data:
        level_data[gid] = {}
    if uid not in level_data[gid]:
        level_data[gid][uid] = {"xp": 0, "level": 0}
    level_data[gid][uid]["xp"] += xp
    old_level = level_data[gid][uid]["level"]
    new_level = int((level_data[gid][uid]["xp"] ** 0.5) / 10)
    if new_level > old_level:
        level_data[gid][uid]["level"] = new_level
        save_json('levels.json', level_data)
        return new_level
    save_json('levels.json', level_data)
    return None

def get_rank(guild_id, user_id):
    gid = str(guild_id)
    uid = str(user_id)
    if gid not in level_data or uid not in level_data[gid]:
        return 0, 0
    return level_data[gid][uid]["level"], level_data[gid][uid]["xp"]

async def check_level_roles(member, new_level):
    gid = str(member.guild.id)
    if gid in level_roles:
        for level, role_id in level_roles[gid].items():
            role = member.guild.get_role(role_id)
            if role and int(level) <= new_level:
                await member.add_roles(role)


ytdl = youtube_dl.YoutubeDL({
    'format': 'bestaudio/best',
    'noplaylist': True,
    'quiet': True,
    'default_search': 'auto',
    'extract_flat': False,
    'postprocessors': [{
        'key': 'FFmpegExtractAudio',
        'preferredcodec': 'mp3',
        'preferredquality': '192',
    }],
})
FFMPEG_OPTIONS = {
    'options': '-vn',
    'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5'
}

class MusicPlayer:
    def __init__(self, ctx):
        self.ctx = ctx
        self.vc = ctx.voice_client
        self.queue = queues.get(ctx.guild.id, [])

    async def play_next(self):
        if self.ctx.guild.id in queues and queues[self.ctx.guild.id]:
            url = queues[self.ctx.guild.id].pop(0)
            print(f"Playing URL: {url}")
            print(f"VC is connected: {self.vc.is_connected()}")
            
            try:
                source = discord.FFmpegOpusAudio(url, **FFMPEG_OPTIONS)
                print("FFmpegOpusAudio source created successfully")
                
                def after_playing(error):
                    print(f"After callback called, error: {error}")
                    if error:
                        print(f'Error playing: {error}')
                    print("Song finished, playing next")
                    coro = self.play_next()
                    fut = asyncio.run_coroutine_threadsafe(coro, bot.loop)
                    try:
                        fut.result()
                    except Exception as e:
                        print(f'Error in after_playing: {e}')
                
                self.vc.play(source, after=after_playing)
                print("vc.play() called successfully")
            except Exception as e:
                print(f'Error creating FFmpegOpusAudio: {e}')
                print(f'Error type: {type(e).__name__}')
                await self.play_next()
        else:
            print("Queue empty, staying in channel")

async def send_log(guild_id, embed):
    if str(guild_id) in log_channels:
        ch = bot.get_channel(log_channels[str(guild_id)])
        if ch:
            await ch.send(embed=embed)


def is_whitelisted(guild_id, user_id, channel_id, content):
    wl = whitelist_data.get(str(guild_id), {})
    member = bot.get_guild(guild_id).get_member(user_id) if bot.get_guild(guild_id) else None
    if member:
        for rid in wl.get('roles', []):
            if member.get_role(rid):
                return True
    if channel_id in wl.get('channels', []):
        return True
    for domain in wl.get('links', []):
        if domain in content:
            return True
    for word in wl.get('words', []):
        if word in content:
            return True
    return False

async def apply_action(ctx, action, member, reason):
    if action == 'warn':
        add_warn(ctx.guild.id, member.id, reason)
        await ctx.send(f"⚠️ {member.mention} warned: {reason}")
    elif action == 'mute':
        await member.timeout(discord.utils.utcnow() + timedelta(minutes=10), reason=reason)
        await ctx.send(f"🔇 {member.mention} muted for 10min: {reason}")
    elif action == 'kick':
        await member.kick(reason=reason)
        await ctx.send(f"👢 {member.mention} kicked: {reason}")
    elif action == 'ban':
        await member.ban(reason=reason)
        await ctx.send(f"🔨 {member.mention} banned: {reason}")
    elif action == 'timeout':
        await member.timeout(discord.utils.utcnow() + timedelta(minutes=5), reason=reason)
        await ctx.send(f"⏱️ {member.mention} timed out: {reason}")


@bot.event
async def on_message(msg):
    if msg.author.bot:
        return
    ctx = await bot.get_context(msg)
    guild = msg.guild
    if not guild:
        await bot.process_commands(msg)
        return

   
    if not msg.content.startswith('*'):
        xp_gain = random.randint(*XP_PER_MESSAGE)
        new_level = add_xp(guild.id, msg.author.id, xp_gain)
        if new_level:
            await check_level_roles(msg.author, new_level)
            try:
                await msg.author.send(f"🎉 You reached level {new_level} in {guild.name}!")
            except:
                pass

    if is_whitelisted(guild.id, msg.author.id, msg.channel.id, msg.content):
        await bot.process_commands(msg)
        return

    
    if config.get('automod', {}).get('invite_action') != 'off':
        if re.search(r'discord\.(gg|com/invite)', msg.content):
            await msg.delete()
            act = config['automod']['invite_action']
            if act != 'delete':
                await apply_action(ctx, act, msg.author, "Discord invite link")
            return

    # Caps
    if len(msg.content) > 10:
        caps = sum(1 for c in msg.content if c.isupper())
        percent = caps / len(msg.content) * 100
        if percent > config['automod'].get('caps_limit', 70):
            act = config['automod'].get('caps_action', 'warn')
            await apply_action(ctx, act, msg.author, f"Excessive caps ({int(percent)}%)")
            return

  
    mention_count = len(msg.mentions)
    if mention_count > config['automod'].get('mention_limit', 5):
        act = config['automod'].get('mention_action', 'warn')
        await apply_action(ctx, act, msg.author, f"Too many mentions ({mention_count})")
        return

    gid = str(guild.id)
    if gid in filter_words:
        if any(word in msg.content.lower() for word in filter_words[gid]):
            await msg.delete()
            await msg.channel.send(f"{msg.author.mention} That word is banned.", delete_after=3)
            return

    
    if config.get('antispam', False):
        now = datetime.utcnow()
        if msg.author.id not in user_messages:
            user_messages[msg.author.id] = []
        user_messages[msg.author.id].append(now)
        user_messages[msg.author.id] = user_messages[msg.author.id][-5:]
        if len(user_messages[msg.author.id]) >= config['automod'].get('spam_count', 5):
            if (user_messages[msg.author.id][-1] - user_messages[msg.author.id][0]).seconds < 3:
                act = config['automod'].get('spam_action', 'timeout')
                await apply_action(ctx, act, msg.author, "Spamming")
                user_messages[msg.author.id] = []
                return

    await bot.process_commands(msg)


class HelpSelect(Select):
    def __init__(self):
        options = [
            discord.SelectOption(label="⚙️ Core", description="Core commands", emoji="⚙️"),
            discord.SelectOption(label="🔨 Moderation", description="Moderation commands", emoji="🔨"),
            discord.SelectOption(label="⚠️ Warnings", description="Warning system", emoji="⚠️"),
            discord.SelectOption(label="🛡️ Security", description="Security commands", emoji="🛡️"),
            discord.SelectOption(label="🎉 Fun & Utility", description="Fun commands", emoji="🎉"),
            discord.SelectOption(label="🎵 Music", description="Music commands", emoji="🎵"),
            discord.SelectOption(label="📝 Auto & Backup", description="Auto and backup", emoji="📝"),
            discord.SelectOption(label="🎫 Tickets", description="Ticket system", emoji="🎫"),
            discord.SelectOption(label="📊 Logs", description="Logging", emoji="📊"),
            discord.SelectOption(label="🤖 Automod", description="Auto-moderation", emoji="🤖"),
            discord.SelectOption(label="✅ Whitelist", description="Whitelist settings", emoji="✅"),
            discord.SelectOption(label="📈 Levels", description="Leveling system", emoji="📈")
        ]
        super().__init__(placeholder="Select a category...", options=options)

    async def callback(self, interaction: discord.Interaction):
        category = self.values[0]
        cmds = help_categories.get(category, {})
        embed = discord.Embed(title=f"{category} Commands", color=discord.Color.blue())
        for name, desc in cmds.items():
            embed.add_field(name=f"*{name}", value=desc, inline=False)
        await interaction.response.edit_message(embed=embed, view=None)

class HelpView(View):
    def __init__(self):
        super().__init__(timeout=60)
        self.add_item(HelpSelect())

help_categories = {
    "⚙️ Core": {"ping": "Bot latency", "help": "This menu", "botinfo": "Bot stats", "userinfo": "User info", "avatar": "Show avatar", "serverinfo": "Server info", "roleinfo": "Role info", "channelinfo": "Channel info"},
    "🔨 Moderation": {"ban": "Ban member", "unban": "Unban user", "kick": "Kick member", "mute": "Timeout", "unmute": "Remove timeout", "purge": "Delete messages", "slowmode": "Set slowmode", "lock": "Lock channel", "unlock": "Unlock channel", "lockdown": "Lock all channels", "unlockdown": "Unlock all", "addrole": "Add role", "removerole": "Remove role", "nick": "Change nickname"},
    "⚠️ Warnings": {"warn": "Warn member", "unwarn": "Remove warning", "warns": "List warnings"},
    "🛡️ Security": {"antilink": "Block invites", "antispam": "Anti-spam", "filter": "Add banned word", "unfilter": "Remove banned word", "setlog": "Set log channel"},
    "🎉 Fun & Utility": {"poll": "Create poll", "announce": "Announcement", "giveaway": "Start giveaway", "reroll": "Reroll giveaway", "say": "Repeat message", "embed": "Send embed", "roll": "Roll dice", "8ball": "Magic 8ball"},
    "🎵 Music": {"play": "Play song", "stop": "Stop and leave", "skip": "Skip current", "queue": "Show queue", "pause": "Pause", "volume": "Set volume"},
    "📝 Auto & Backup": {"autorole": "Auto-role", "setwelcome": "Set welcome message (use {member_mention} and {server})", "setgoodbye": "Set goodbye message", "backup": "Backup server", "restore": "Restore from backup"},
    "🎫 Tickets": {"ticket_panel": "Create ticket panel", "ticket": "Open ticket (button)", "close": "Close ticket"},
    "📊 Logs": {"setup_logs": "Create all log channels"},
    "🤖 Automod": {"automod caps": "Caps limit", "automod mentions": "Mention limit", "automod invites": "Invite action", "automod spam": "Spam threshold"},
    "✅ Whitelist": {"whitelist link/word/role/channel": "Add to whitelist", "unwhitelist link/word/role/channel": "Remove from whitelist"},
    "📈 Levels": {"rank": "Your rank", "leaderboard": "Top 10", "setlevelrole": "Assign role at level", "removelevelrole": "Remove level role"}
}

@bot.command()
async def help(ctx, command_name: str = None):
    """Shows help menu with dropdown or details of a specific command."""
    if command_name:
        cmd = bot.get_command(command_name)
        if cmd:
            # Build clean usage string
            params = []
            for name, param in cmd.clean_params.items():
                if param.kind == param.KEYWORD_ONLY:
                    params.append(f"<{name}>")
                elif param.default == param.empty:
                    params.append(f"<{name}>")
                else:
                    params.append(f"[{name}]")
            usage = f"*{cmd.name} {' '.join(params)}"
            doc = cmd.help or "No description available."
            embed = discord.Embed(title=f"*{cmd.name}", description=doc, color=discord.Color.blue())
            embed.add_field(name="Usage", value=f"`{usage}`", inline=False)
            await ctx.send(embed=embed)
        else:
            await ctx.send(f"Command `{command_name}` not found.")
        return

    embed = discord.Embed(title="Bot Help", description="Select a category from the dropdown below.", color=discord.Color.blue())
    await ctx.send(embed=embed, view=HelpView())


@bot.command()
async def ping(ctx):
    await ctx.send(f"Pong! {round(bot.latency*1000)}ms")

@bot.command()
async def botinfo(ctx):
    embed = discord.Embed(title="Bot Info", color=discord.Color.green())
    embed.add_field(name="Latency", value=f"{round(bot.latency*1000)}ms", inline=True)
    embed.add_field(name="Servers", value=len(bot.guilds), inline=True)
    embed.add_field(name="Commands", value=len(bot.commands), inline=True)
    await ctx.send(embed=embed)

@bot.command()
async def userinfo(ctx, member: discord.Member = None):
    member = member or ctx.author
    embed = discord.Embed(title=f"{member.display_name}", color=member.color)
    embed.add_field(name="ID", value=member.id, inline=True)
    embed.add_field(name="Joined", value=member.joined_at.strftime("%Y-%m-%d"), inline=True)
    embed.add_field(name="Created", value=member.created_at.strftime("%Y-%m-%d"), inline=True)
    embed.add_field(name="Top role", value=member.top_role.mention, inline=True)
    embed.set_thumbnail(url=member.avatar.url if member.avatar else member.default_avatar.url)
    await ctx.send(embed=embed)

@bot.command()
async def avatar(ctx, member: discord.Member = None):
    member = member or ctx.author
    embed = discord.Embed(title=f"{member.display_name}'s avatar", color=discord.Color.blue())
    embed.set_image(url=member.avatar.url if member.avatar else member.default_avatar.url)
    await ctx.send(embed=embed)

@bot.command()
async def serverinfo(ctx):
    embed = discord.Embed(title=ctx.guild.name, color=discord.Color.blue())
    embed.add_field(name="Owner", value=ctx.guild.owner.mention, inline=True)
    embed.add_field(name="Members", value=ctx.guild.member_count, inline=True)
    embed.add_field(name="Channels", value=len(ctx.guild.channels), inline=True)
    embed.add_field(name="Roles", value=len(ctx.guild.roles), inline=True)
    embed.add_field(name="Boost level", value=ctx.guild.premium_tier, inline=True)
    embed.set_thumbnail(url=ctx.guild.icon.url if ctx.guild.icon else None)
    await ctx.send(embed=embed)

@bot.command()
async def roleinfo(ctx, role: discord.Role):
    embed = discord.Embed(title=role.name, color=role.color)
    embed.add_field(name="ID", value=role.id, inline=True)
    embed.add_field(name="Members", value=len(role.members), inline=True)
    embed.add_field(name="Color", value=str(role.color), inline=True)
    embed.add_field(name="Position", value=role.position, inline=True)
    await ctx.send(embed=embed)

@bot.command()
async def channelinfo(ctx, channel: discord.TextChannel = None):
    channel = channel or ctx.channel
    embed = discord.Embed(title=channel.name, color=discord.Color.blue())
    embed.add_field(name="ID", value=channel.id, inline=True)
    embed.add_field(name="Category", value=channel.category.name if channel.category else "None", inline=True)
    embed.add_field(name="Slowmode", value=f"{channel.slowmode_delay}s", inline=True)
    embed.add_field(name="Created", value=channel.created_at.strftime("%Y-%m-%d"), inline=True)
    await ctx.send(embed=embed)


@bot.command()
@commands.has_permissions(ban_members=True)
async def ban(ctx, member: discord.Member, *, reason=None):
    await member.ban(reason=reason)
    embed = discord.Embed(title="Banned", description=f"{member.mention} banned.\nReason: {reason or 'None'}", color=discord.Color.red())
    await ctx.send(embed=embed)

@bot.command()
@commands.has_permissions(ban_members=True)
async def unban(ctx, *, name):
    banned = [entry async for entry in ctx.guild.bans()]
    for entry in banned:
        if str(entry.user) == name or entry.user.name == name:
            await ctx.guild.unban(entry.user)
            embed = discord.Embed(title="Unbanned", description=f"{entry.user.mention} unbanned.", color=discord.Color.green())
            await ctx.send(embed=embed)
            return
    await ctx.send("User not found.")

@bot.command()
@commands.has_permissions(kick_members=True)
async def kick(ctx, member: discord.Member, *, reason=None):
    await member.kick(reason=reason)
    embed = discord.Embed(title="Kicked", description=f"{member.mention} kicked.\nReason: {reason or 'None'}", color=discord.Color.orange())
    await ctx.send(embed=embed)

@bot.command()
@commands.has_permissions(moderate_members=True)
async def mute(ctx, member: discord.Member, minutes: int = 60, *, reason=None):
    await member.timeout(discord.utils.utcnow() + timedelta(minutes=minutes), reason=reason)
    embed = discord.Embed(title="Muted", description=f"{member.mention} muted for {minutes} min.\nReason: {reason or 'None'}", color=discord.Color.dark_grey())
    await ctx.send(embed=embed)

@bot.command()
@commands.has_permissions(moderate_members=True)
async def unmute(ctx, member: discord.Member, *, reason=None):
    await member.timeout(None, reason=reason)
    embed = discord.Embed(title="Unmuted", description=f"{member.mention} unmuted.\nReason: {reason or 'None'}", color=discord.Color.green())
    await ctx.send(embed=embed)

@bot.command()
@commands.has_permissions(manage_messages=True)
async def purge(ctx, amount: int):
    amount = min(max(amount, 1), 100)
    deleted = await ctx.channel.purge(limit=amount+1)
    await ctx.send(f"Deleted {len(deleted)-1} messages.", delete_after=3)

@bot.command()
@commands.has_permissions(manage_channels=True)
async def slowmode(ctx, seconds: int):
    await ctx.channel.edit(slowmode_delay=seconds)
    await ctx.send(f"Slowmode set to {seconds}s.")

@bot.command()
@commands.has_permissions(manage_channels=True)
async def lock(ctx, channel: discord.TextChannel = None):
    channel = channel or ctx.channel
    await channel.set_permissions(ctx.guild.default_role, send_messages=False)
    embed = discord.Embed(title="Locked", description=f"{channel.mention} locked.", color=discord.Color.red())
    await ctx.send(embed=embed)

@bot.command()
@commands.has_permissions(manage_channels=True)
async def unlock(ctx, channel: discord.TextChannel = None):
    channel = channel or ctx.channel
    await channel.set_permissions(ctx.guild.default_role, send_messages=None)
    embed = discord.Embed(title="Unlocked", description=f"{channel.mention} unlocked.", color=discord.Color.green())
    await ctx.send(embed=embed)

@bot.command()
@commands.has_permissions(administrator=True)
async def lockdown(ctx):
    for ch in ctx.guild.text_channels:
        await ch.set_permissions(ctx.guild.default_role, send_messages=False)
    await ctx.send("Server locked down.")

@bot.command()
@commands.has_permissions(administrator=True)
async def unlockdown(ctx):
    for ch in ctx.guild.text_channels:
        await ch.set_permissions(ctx.guild.default_role, send_messages=None)
    await ctx.send("Server unlocked.")

@bot.command()
@commands.has_permissions(manage_roles=True)
async def addrole(ctx, member: discord.Member, role: discord.Role):
    await member.add_roles(role)
    await ctx.send(f"Added {role.mention} to {member.mention}.")

@bot.command()
@commands.has_permissions(manage_roles=True)
async def removerole(ctx, member: discord.Member, role: discord.Role):
    await member.remove_roles(role)
    await ctx.send(f"Removed {role.mention} from {member.mention}.")

@bot.command()
@commands.has_permissions(manage_nicknames=True)
async def nick(ctx, member: discord.Member, *, new=None):
    await member.edit(nick=new)
    await ctx.send(f"Changed nickname for {member.mention} to {new}.")


def add_warn(guild_id, user_id, reason):
    data = warnings
    gid = str(guild_id)
    uid = str(user_id)
    if gid not in data:
        data[gid] = {}
    if uid not in data[gid]:
        data[gid][uid] = []
    data[gid][uid].append({"id": len(data[gid][uid])+1, "reason": reason})
    save_json('warnings.json', data)

def get_warns(guild_id, user_id):
    return warnings.get(str(guild_id), {}).get(str(user_id), [])

def remove_warn(guild_id, user_id, warn_id):
    data = warnings
    gid = str(guild_id)
    uid = str(user_id)
    if gid in data and uid in data[gid]:
        for i, w in enumerate(data[gid][uid]):
            if w["id"] == warn_id:
                del data[gid][uid][i]
                for idx, w2 in enumerate(data[gid][uid], 1):
                    w2["id"] = idx
                save_json('warnings.json', data)
                return True
    return False

@bot.command()
@commands.has_permissions(kick_members=True)
async def warn(ctx, member: discord.Member, *, reason="No reason"):
    add_warn(ctx.guild.id, member.id, reason)
    embed = discord.Embed(title="Warned", description=f"{member.mention} warned.\nReason: {reason}", color=discord.Color.orange())
    await ctx.send(embed=embed)

@bot.command()
@commands.has_permissions(kick_members=True)
async def unwarn(ctx, member: discord.Member, warn_id: int):
    if remove_warn(ctx.guild.id, member.id, warn_id):
        await ctx.send(f"Removed warning #{warn_id} from {member.mention}.")
    else:
        await ctx.send("Warning not found.")

@bot.command()
@commands.has_permissions(kick_members=True)
async def warns(ctx, member: discord.Member):
    warns_list = get_warns(ctx.guild.id, member.id)
    if not warns_list:
        await ctx.send(f"No warnings for {member.mention}.")
        return
    desc = "\n".join([f"`#{w['id']}`: {w['reason']}" for w in warns_list])
    embed = discord.Embed(title=f"Warnings for {member.display_name}", description=desc, color=discord.Color.orange())
    await ctx.send(embed=embed)


@bot.command()
@commands.has_permissions(administrator=True)
async def antilink(ctx, mode: str = None):
    if not mode:
        cur = config.get('antilink', False)
        await ctx.send(f"Anti-link is {'ON' if cur else 'OFF'}")
        return
    config['antilink'] = mode.lower() == 'on'
    save_json('config.json', config)
    await ctx.send(f"Anti-link turned {mode}.")

@bot.command()
@commands.has_permissions(administrator=True)
async def antispam(ctx, mode: str = None):
    if not mode:
        cur = config.get('antispam', False)
        await ctx.send(f"Anti-spam is {'ON' if cur else 'OFF'}")
        return
    config['antispam'] = mode.lower() == 'on'
    save_json('config.json', config)
    await ctx.send(f"Anti-spam turned {mode}.")

@bot.command()
@commands.has_permissions(administrator=True)
async def filter(ctx, word: str):
    gid = str(ctx.guild.id)
    if gid not in filter_words:
        filter_words[gid] = []
    if word.lower() not in filter_words[gid]:
        filter_words[gid].append(word.lower())
        save_json('filter_words.json', filter_words)
        await ctx.send(f"Added `{word}` to filter.")
    else:
        await ctx.send("Word already filtered.")

@bot.command()
@commands.has_permissions(administrator=True)
async def unfilter(ctx, word: str):
    gid = str(ctx.guild.id)
    if gid in filter_words and word.lower() in filter_words[gid]:
        filter_words[gid].remove(word.lower())
        save_json('filter_words.json', filter_words)
        await ctx.send(f"Removed `{word}` from filter.")
    else:
        await ctx.send("Word not in filter.")

@bot.command()
@commands.has_permissions(administrator=True)
async def setlog(ctx, channel: discord.TextChannel):
    log_channels[str(ctx.guild.id)] = channel.id
    save_json('log_channels.json', log_channels)
    await ctx.send(f"Log channel set to {channel.mention}")


@bot.command()
async def poll(ctx, question, *options):
    if len(options) < 2 or len(options) > 9:
        await ctx.send("Need 2-9 options.")
        return
    desc = "\n".join([f"{i+1}. {opt}" for i, opt in enumerate(options)])
    embed = discord.Embed(title=f"🗳️ {question}", description=desc, color=discord.Color.purple())
    msg = await ctx.send(embed=embed)
    reacts = ["1️⃣","2️⃣","3️⃣","4️⃣","5️⃣","6️⃣","7️⃣","8️⃣","9️⃣"]
    for i in range(len(options)):
        await msg.add_reaction(reacts[i])

@bot.command()
@commands.has_permissions(administrator=True)
async def announce(ctx, channel: discord.TextChannel, *, message):
    embed = discord.Embed(title="📢 Announcement", description=message, color=discord.Color.blue())
    await channel.send(embed=embed)
    await ctx.send(f"Sent to {channel.mention}", delete_after=3)

@bot.command()
async def say(ctx, *, msg):
    await ctx.send(msg)
    await ctx.message.delete()

@bot.command()
async def embed(ctx, title, *, description):
    embed = discord.Embed(title=title, description=description, color=discord.Color.blue())
    await ctx.send(embed=embed)

@bot.command()
async def roll(ctx, sides: int = 6):
    await ctx.send(f"🎲 You rolled {random.randint(1, sides)}")

@bot.command()
async def eightball(ctx, *, question):
    answers = ["Yes", "No", "Maybe", "Ask later", "Definitely", "Never"]
    await ctx.send(f"🎱 {random.choice(answers)}")


@bot.command()
@commands.has_permissions(administrator=True)
async def giveaway(ctx, duration: str, *, prize):
    seconds = 0
    if duration.endswith('s'):
        seconds = int(duration[:-1])
    elif duration.endswith('m'):
        seconds = int(duration[:-1])*60
    elif duration.endswith('h'):
        seconds = int(duration[:-1])*3600
    elif duration.endswith('d'):
        seconds = int(duration[:-1])*86400
    else:
        await ctx.send("Use format: 10s, 5m, 2h, 1d")
        return
    end = datetime.utcnow() + timedelta(seconds=seconds)
    embed = discord.Embed(title="🎉 Giveaway!", description=f"Prize: {prize}\nEnds: <t:{int(end.timestamp())}:R>", color=discord.Color.gold())
    msg = await ctx.send(embed=embed)
    await msg.add_reaction("🎉")
    gid = str(ctx.guild.id)
    giveaways[gid] = giveaways.get(gid, [])
    giveaways[gid].append({"message_id": msg.id, "channel_id": ctx.channel.id, "prize": prize, "end": end.timestamp()})
    save_json('giveaways.json', giveaways)

    await asyncio.sleep(seconds)
    final_msg = await ctx.channel.fetch_message(msg.id)
    users = []
    for reaction in final_msg.reactions:
        if str(reaction.emoji) == "🎉":
            async for user in reaction.users():
                if not user.bot:
                    users.append(user)
    if users:
        winner = random.choice(users)
        await ctx.send(f"🎉 Winner of {prize}: {winner.mention}!")
    else:
        await ctx.send(f"No participants for {prize}.")

@bot.command()
@commands.has_permissions(administrator=True)
async def reroll(ctx, message_id: int):
    try:
        msg = await ctx.channel.fetch_message(message_id)
        users = []
        for reaction in msg.reactions:
            if str(reaction.emoji) == "🎉":
                async for user in reaction.users():
                    if not user.bot:
                        users.append(user)
        if users:
            winner = random.choice(users)
            await ctx.send(f"🎉 New winner: {winner.mention}!")
        else:
            await ctx.send("No valid participants.")
    except Exception as e:
        print(f"Error in reroll: {e}")
        await ctx.send("Could not find that giveaway message.")

@bot.command()
async def play(ctx, *, query: str = None):
    """Plays a song - Bot automatically joins the user's voice channel"""
    if not query:
        return await ctx.send("❌ Please provide a song name or link! Example: `r!play bad guy`")

    if not ctx.author.voice:
        return await ctx.send("❌ You must be in a **voice channel** to use this command!")

    voice_channel = ctx.author.voice.channel
    vc = ctx.voice_client

  
    if not vc:
        try:
            vc = await voice_channel.connect()
            await ctx.send(f"✅ Connected to **{voice_channel.name}**")
        except Exception as e:
            return await ctx.send(f"❌ Could not join the voice channel: {e}")
    elif vc.channel != voice_channel:
        try:
            await vc.move_to(voice_channel)
            await ctx.send(f"🔄 Moved to **{voice_channel.name}**")
        except:
            pass

    async with ctx.typing():
        try:
            data = await asyncio.get_event_loop().run_in_executor(
                None, lambda: ytdl.extract_info(query, download=False)
            )

            if 'entries' in data:          
                data = data['entries'][0]

            
            if 'formats' in data:
             
                audio_format = next((f for f in data['formats'] if f.get('acodec') != 'none' and f.get('vcodec') == 'none'), None)
                if not audio_format:
                    audio_format = next((f for f in data['formats'] if f.get('acodec') != 'none'), None)
                if audio_format:
                    url = audio_format['url']
                else:
                    url = data['url']
            else:
                url = data['url']
            
            title = data.get('title', 'Unknown Title')

        except Exception as e:
            return await ctx.send(f"❌ Could not find the song: {str(e)[:100]}")

   
    if ctx.guild.id not in queues:
        queues[ctx.guild.id] = []

    queues[ctx.guild.id].append(url)
    print(f"DEBUG: URL added: {url[:50]}...")

    
    if not vc.is_playing() and not vc.is_paused():
        print("DEBUG: Starting playback")
        player = MusicPlayer(ctx)
        player.vc = vc
        print(f"DEBUG: Queue size: {len(queues[ctx.guild.id])}")
        await player.play_next()
        await ctx.send(f"▶️ **Now Playing:** {title}")
    else:
        await ctx.send(f"📥 Added to queue: **{title}**")

@bot.command()
async def stop(ctx):
    if ctx.voice_client:
        await ctx.voice_client.disconnect()
        queues.pop(ctx.guild.id, None)
        await ctx.send("Stopped and left.")
    else:
        await ctx.send("Not in a voice channel.")

@bot.command()
async def skip(ctx):
    if ctx.voice_client and ctx.voice_client.is_playing():
        ctx.voice_client.stop()
        await ctx.send("Skipped.")
    else:
        await ctx.send("Nothing playing.")

@bot.command()
async def queue(ctx):
    q = queues.get(ctx.guild.id, [])
    if not q:
        await ctx.send("Queue empty.")
        return
    await ctx.send(f"Queue: {len(q)} songs")

@bot.command()
async def pause(ctx):
    if ctx.voice_client and ctx.voice_client.is_playing():
        ctx.voice_client.pause()
        await ctx.send("Paused.")

@bot.command()
async def volume(ctx, vol: int):
    if ctx.voice_client and ctx.voice_client.source:
        ctx.voice_client.source.volume = vol / 100
        await ctx.send(f"Volume set to {vol}%")
    else:
        await ctx.send("Not playing anything.")


@bot.command()
@commands.has_permissions(administrator=True)
async def autorole(ctx, role: discord.Role = None):
    gid = str(ctx.guild.id)
    if role is None:
        if gid in autorole_data:
            del autorole_data[gid]
            save_json('autorole.json', autorole_data)
            await ctx.send("Autorole disabled.")
        else:
            await ctx.send("No autorole set.")
        return
    autorole_data[gid] = role.id
    save_json('autorole.json', autorole_data)
    await ctx.send(f"New members will get {role.mention}")


@bot.command()
@commands.has_permissions(administrator=True)
async def setwelcome(ctx, channel: discord.TextChannel, *, message):
    """
    Set welcome message. Use {member_mention} to mention the new member, and {server} for server name.
    For an embed, separate title and description with '|'
    Example: *setwelcome #welcome Welcome {member_mention} to {server}!
    """
    if '|' in message:
        parts = message.split('|', 1)
        title = parts[0].strip()
        desc = parts[1].strip()
        embed_data = {"title": title, "description": desc}
        welcome_data[str(ctx.guild.id)] = {
            "channel": channel.id,
            "embed": embed_data
        }
    else:
        welcome_data[str(ctx.guild.id)] = {
            "channel": channel.id,
            "text": message
        }
    save_json('welcome.json', welcome_data)
    await ctx.send(f"Welcome message set in {channel.mention}")

@bot.command()
@commands.has_permissions(administrator=True)
async def setgoodbye(ctx, channel: discord.TextChannel, *, message):
    goodbye_data[str(ctx.guild.id)] = {"channel": channel.id, "text": message}
    save_json('goodbye.json', goodbye_data)
    await ctx.send(f"Goodbye message set in {channel.mention}")

@bot.event
async def on_member_join(member):
    gid = str(member.guild.id)
    if gid in autorole_data:
        role = member.guild.get_role(autorole_data[gid])
        if role:
            await member.add_roles(role)
    if gid in welcome_data:
        data = welcome_data[gid]
        channel = member.guild.get_channel(data['channel'])
        if channel:
            if 'embed' in data:
                embed = discord.Embed(
                    title=data['embed']['title'].replace('{member_mention}', member.mention).replace('{server}', member.guild.name),
                    description=data['embed']['description'].replace('{member_mention}', member.mention).replace('{server}', member.guild.name),
                    color=discord.Color.green()
                )
                await channel.send(embed=embed)
            else:
                msg = data['text'].replace('{member_mention}', member.mention).replace('{server}', member.guild.name)
                await channel.send(msg)

@bot.event
async def on_member_remove(member):
    gid = str(member.guild.id)
    if gid in goodbye_data:
        data = goodbye_data[gid]
        channel = member.guild.get_channel(data['channel'])
        if channel:
            msg = data['text'].replace('{member}', member.name).replace('{server}', member.guild.name)
            await channel.send(msg)

@bot.command()
@commands.has_permissions(administrator=True)
async def backup(ctx):
    data = {
        "roles": [(r.name, r.permissions.value, r.color.value, r.hoist, r.mentionable) for r in ctx.guild.roles if r.name != "@everyone"],
        "channels": [(ch.name, ch.type.name, ch.position) for ch in ctx.guild.channels],
        "settings": {"name": ctx.guild.name}
    }
    with open(f"backup_{ctx.guild.id}.json", "w") as f:
        json.dump(data, f, indent=4)
    await ctx.send("Backup saved. Download the file from server storage.")

@bot.command()
@commands.has_permissions(administrator=True)
async def restore(ctx):
    try:
        with open(f"backup_{ctx.guild.id}.json", "r") as f:
            data = json.load(f)
        await ctx.guild.edit(name=data['settings']['name'])
        await ctx.send("Restored basic settings. Roles and channels require manual creation due to Discord limits.")
    except:
        await ctx.send("No backup file found for this server.")


class TicketButton(Button):
    def __init__(self):
        super().__init__(label="Create Ticket", style=discord.ButtonStyle.green, emoji="🎫")

    async def callback(self, interaction: discord.Interaction):
        guild = interaction.guild
        category = None
        if 'ticket_category' in config:
            category = discord.utils.get(guild.categories, id=config['ticket_category'])
        if not category:
            category = discord.utils.get(guild.categories, name="Tickets")
            if not category:
                category = await guild.create_category("Tickets")
                config['ticket_category'] = category.id
                save_json('config.json', config)
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(read_messages=False),
            interaction.user: discord.PermissionOverwrite(read_messages=True, send_messages=True),
            guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True)
        }
        ticket_number = len([ch for ch in category.channels if ch.name.startswith("ticket-")]) + 1
        channel = await category.create_text_channel(f"ticket-{ticket_number}", overwrites=overwrites)
        embed = discord.Embed(title=f"Ticket #{ticket_number}", description=f"Opened by {interaction.user.mention}\nUse `*close` to close.", color=discord.Color.green())
        await channel.send(embed=embed)
        await interaction.response.send_message(f"Ticket created: {channel.mention}", ephemeral=True)
        tickets[str(channel.id)] = {"user": interaction.user.id, "created": datetime.utcnow().isoformat()}
        save_json('tickets.json', tickets)

class TicketView(View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(TicketButton())

@bot.command()
@commands.has_permissions(administrator=True)
async def ticket_panel(ctx, channel: discord.TextChannel):
    """Send the ticket panel with button to a channel."""
    embed = discord.Embed(title="Support Tickets", description="Click the button below to create a support ticket.", color=discord.Color.blue())
    await channel.send(embed=embed, view=TicketView())
    await ctx.send(f"Ticket panel sent to {channel.mention}")

@bot.command()
@commands.has_permissions(manage_channels=True)
async def close(ctx):
    if ctx.channel.name.startswith("ticket-"):
        await ctx.send("Closing ticket in 5 seconds...")
        await asyncio.sleep(5)
        await ctx.channel.delete()
    else:
        await ctx.send("This is not a ticket channel.")


@bot.command()
@commands.has_permissions(administrator=True)
async def setup_logs(ctx):
    guild = ctx.guild
    categories = {
        "Moderation Logs": ["mod-actions", "mod-logs"],
        "Message Logs": ["message-edits", "message-deletes"],
        "Member Logs": ["join-logs", "leave-logs"],
        "Voice Logs": ["voice-logs"],
        "Automod Logs": ["automod-logs"]
    }
    created = []
    for cat_name, ch_names in categories.items():
        category = discord.utils.get(guild.categories, name=cat_name)
        if not category:
            category = await guild.create_category(cat_name)
        for ch_name in ch_names:
            existing = discord.utils.get(guild.text_channels, name=ch_name)
            if not existing:
                ch = await guild.create_text_channel(ch_name, category=category)
                created.append(ch.mention)
    if created:
        await ctx.send(f"Created log channels: {', '.join(created)}")
    else:
        await ctx.send("All log channels already exist.")


@bot.group()
@commands.has_permissions(administrator=True)
async def automod(ctx):
    if ctx.invoked_subcommand is None:
        settings = config.get('automod', {})
        embed = discord.Embed(title="Automod Settings", color=discord.Color.blue())
        for k, v in settings.items():
            embed.add_field(name=k, value=v, inline=False)
        await ctx.send(embed=embed)

@automod.command()
async def caps(ctx, limit: int, action: str = None):
    config['automod']['caps_limit'] = limit
    if action and action in ['warn','mute','kick','ban']:
        config['automod']['caps_action'] = action
    save_json('config.json', config)
    await ctx.send(f"Caps limit set to {limit}%, action: {config['automod']['caps_action']}")

@automod.command()
async def mentions(ctx, limit: int, action: str = None):
    config['automod']['mention_limit'] = limit
    if action and action in ['warn','mute','kick','ban']:
        config['automod']['mention_action'] = action
    save_json('config.json', config)
    await ctx.send(f"Mention limit set to {limit}, action: {config['automod']['mention_action']}")

@automod.command()
async def invites(ctx, action: str):
    if action not in ['delete','warn','mute','kick','ban','off']:
        await ctx.send("Invalid action.")
        return
    config['automod']['invite_action'] = action
    save_json('config.json', config)
    await ctx.send(f"Invite filter action: {action}")

@automod.command()
async def spam(ctx, count: int, action: str):
    if action not in ['timeout','warn','mute','kick','ban']:
        await ctx.send("Invalid action.")
        return
    config['automod']['spam_count'] = count
    config['automod']['spam_action'] = action
    save_json('config.json', config)
    await ctx.send(f"Spam threshold: {count} messages/3s, action: {action}")


@bot.group(name='whitelist')
@commands.has_permissions(administrator=True)
async def whitelist_cmd(ctx):
    if ctx.invoked_subcommand is None:
        wl = whitelist_data.get(str(ctx.guild.id), {})
        embed = discord.Embed(title="Whitelist", color=discord.Color.green())
        embed.add_field(name="Links", value="\n".join(wl.get('links', [])) or "None", inline=False)
        embed.add_field(name="Words", value="\n".join(wl.get('words', [])) or "None", inline=False)
        embed.add_field(name="Roles", value="\n".join([f"<@&{rid}>" for rid in wl.get('roles', [])]) or "None", inline=False)
        embed.add_field(name="Channels", value="\n".join([f"<#{cid}>" for cid in wl.get('channels', [])]) or "None", inline=False)
        await ctx.send(embed=embed)

@whitelist_cmd.command(name='link')
async def whitelist_link(ctx, domain: str):
    gid = str(ctx.guild.id)
    if gid not in whitelist_data:
        whitelist_data[gid] = {}
    if 'links' not in whitelist_data[gid]:
        whitelist_data[gid]['links'] = []
    if domain.lower() not in whitelist_data[gid]['links']:
        whitelist_data[gid]['links'].append(domain.lower())
        save_json('whitelist.json', whitelist_data)
        await ctx.send(f"Added `{domain}` to link whitelist.")
    else:
        await ctx.send("Domain already whitelisted.")

@whitelist_cmd.command(name='word')
async def whitelist_word(ctx, word: str):
    gid = str(ctx.guild.id)
    if gid not in whitelist_data:
        whitelist_data[gid] = {}
    if 'words' not in whitelist_data[gid]:
        whitelist_data[gid]['words'] = []
    if word.lower() not in whitelist_data[gid]['words']:
        whitelist_data[gid]['words'].append(word.lower())
        save_json('whitelist.json', whitelist_data)
        await ctx.send(f"Added `{word}` to word whitelist.")
    else:
        await ctx.send("Word already whitelisted.")

@whitelist_cmd.command(name='role')
async def whitelist_role(ctx, role: discord.Role):
    gid = str(ctx.guild.id)
    if gid not in whitelist_data:
        whitelist_data[gid] = {}
    if 'roles' not in whitelist_data[gid]:
        whitelist_data[gid]['roles'] = []
    if role.id not in whitelist_data[gid]['roles']:
        whitelist_data[gid]['roles'].append(role.id)
        save_json('whitelist.json', whitelist_data)
        await ctx.send(f"Added {role.mention} to whitelist.")
    else:
        await ctx.send("Role already whitelisted.")

@whitelist_cmd.command(name='channel')
async def whitelist_channel(ctx, channel: discord.TextChannel):
    gid = str(ctx.guild.id)
    if gid not in whitelist_data:
        whitelist_data[gid] = {}
    if 'channels' not in whitelist_data[gid]:
        whitelist_data[gid]['channels'] = []
    if channel.id not in whitelist_data[gid]['channels']:
        whitelist_data[gid]['channels'].append(channel.id)
        save_json('whitelist.json', whitelist_data)
        await ctx.send(f"Added {channel.mention} to whitelist.")
    else:
        await ctx.send("Channel already whitelisted.")

@bot.group()
async def unwhitelist(ctx):
    if ctx.invoked_subcommand is None:
        await ctx.send("Usage: *unwhitelist link <domain>, *unwhitelist word <word>, *unwhitelist role @role, *unwhitelist channel #channel")

@unwhitelist.command(name='link')
async def unwhitelist_link(ctx, domain: str):
    gid = str(ctx.guild.id)
    if gid in whitelist_data and 'links' in whitelist_data[gid]:
        if domain.lower() in whitelist_data[gid]['links']:
            whitelist_data[gid]['links'].remove(domain.lower())
            save_json('whitelist.json', whitelist_data)
            await ctx.send(f"Removed `{domain}` from whitelist.")

@unwhitelist.command(name='word')
async def unwhitelist_word(ctx, word: str):
    gid = str(ctx.guild.id)
    if gid in whitelist_data and 'words' in whitelist_data[gid]:
        if word.lower() in whitelist_data[gid]['words']:
            whitelist_data[gid]['words'].remove(word.lower())
            save_json('whitelist.json', whitelist_data)
            await ctx.send(f"Removed `{word}` from whitelist.")

@unwhitelist.command(name='role')
async def unwhitelist_role(ctx, role: discord.Role):
    gid = str(ctx.guild.id)
    if gid in whitelist_data and 'roles' in whitelist_data[gid]:
        if role.id in whitelist_data[gid]['roles']:
            whitelist_data[gid]['roles'].remove(role.id)
            save_json('whitelist.json', whitelist_data)
            await ctx.send(f"Removed {role.mention} from whitelist.")

@unwhitelist.command(name='channel')
async def unwhitelist_channel(ctx, channel: discord.TextChannel):
    gid = str(ctx.guild.id)
    if gid in whitelist_data and 'channels' in whitelist_data[gid]:
        if channel.id in whitelist_data[gid]['channels']:
            whitelist_data[gid]['channels'].remove(channel.id)
            save_json('whitelist.json', whitelist_data)
            await ctx.send(f"Removed {channel.mention} from whitelist.")


@bot.command()
async def rank(ctx, member: discord.Member = None):
    member = member or ctx.author
    level, xp = get_rank(ctx.guild.id, member.id)
    next_level_xp = ((level + 1) * 10) ** 2
    embed = discord.Embed(title=f"Level of {member.display_name}", color=discord.Color.gold())
    embed.add_field(name="Level", value=level, inline=True)
    embed.add_field(name="XP", value=f"{xp}/{next_level_xp}", inline=True)
    await ctx.send(embed=embed)

@bot.command()
async def leaderboard(ctx):
    gid = str(ctx.guild.id)
    if gid not in level_data:
        await ctx.send("No level data yet.")
        return
    users = sorted(level_data[gid].items(), key=lambda x: x[1]["xp"], reverse=True)[:10]
    if not users:
        await ctx.send("No data.")
        return
    desc = "\n".join([f"**{i+1}.** <@{uid}> - Level {data['level']} ({data['xp']} XP)" for i, (uid, data) in enumerate(users)])
    embed = discord.Embed(title="Leaderboard", description=desc, color=discord.Color.gold())
    await ctx.send(embed=embed)

@bot.command()
@commands.has_permissions(administrator=True)
async def setlevelrole(ctx, level: int, role: discord.Role):
    gid = str(ctx.guild.id)
    if gid not in level_roles:
        level_roles[gid] = {}
    level_roles[gid][level] = role.id
    save_json('level_roles.json', level_roles)
    await ctx.send(f"Role {role.mention} will be given at level {level}.")

@bot.command()
@commands.has_permissions(administrator=True)
async def removelevelrole(ctx, level: int):
    gid = str(ctx.guild.id)
    if gid in level_roles and level in level_roles[gid]:
        del level_roles[gid][level]
        save_json('level_roles.json', level_roles)
        await ctx.send(f"Removed level role for level {level}.")
    else:
        await ctx.send("Level role not found.")


@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("You don't have permission to use this command.")
    elif isinstance(error, commands.BadArgument):
        await ctx.send("Invalid argument (user, role, channel, etc.)")
    elif isinstance(error, commands.MissingRequiredArgument):
        await ctx.send(f"Missing argument. Use `*help {ctx.command.name}`")
    else:
        raise error



bot.run('MTQ5ODU0OTk3NzMzNTcyNjA5MQ.GYwY1A.JSxxLG7JsTaBaYOhTtQbDCpPgE3YsO2hb6l9cY')
