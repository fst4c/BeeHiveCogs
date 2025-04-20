import discord
from redbot.core import commands, Config
import typing
import os
from datetime import timedelta
import asyncio
import random

class Honeypot(commands.Cog, name="Honeypot"):
    """Create a channel at the top of the server to attract self bots/scammers and notify/mute/kick/ban them immediately!"""

    SCAM_TYPES = {
        "nitro": [
            "nitro", "free nitro", "discord nitro", "gift nitro", "nitro giveaway", "nitro drop", "nitro for free", "nitro rewards", "nitro event", "nitro boost",
            "nitro-promo", "nitro-promotion", "nitro code", "nitro claim", "nitro link", "nitro-discord", "discordnitro", "nitro_gift",
            "discord.com/gifts", "discord.gift", "discordapp.com/gift", "discord.com/nitro", "discord.com/gift", "discord.com/claim", "discord.com/activate",
            "discord.com/verify", "discord-nitro.com", "discordnitro.com", "nitro promo", "nitro scam", "nitro hack", "nitro generator", "nitro airdrop"
        ],
        "steam": [
            "steam", "steam gift", "steam code", "steam wallet", "steamcommunity", "steam offer", "steamcard", "steamcards", "steam-gift", "steam-cards",
            "steamcommunity.com", "steam account", "steam scam", "steam trade", "steam redeem", "steam balance", "steamcredit", "steam credits", "steam $",
            "steam voucher", "steam free", "steam promo", "steam bonus", "$50", "50$", "$100", "100$", "steam airdrop", "steam generator", "steam hack",
            "steam giveaway", "steam event", "steamcommunity.com/gift", "steamcommunity.com/tradeoffer", "steamcommunity.com/id/", "steamcommunity.com/profiles/",
            "steamcommunity.com/market"
        ],
        "csam": [
            "nude", "nudes", "teen", "teens", "underage", "cp", "loli", "jailbait", "13yo", "14yo", "15yo", "16yo", "17yo", "minor", "preteen", "child porn",
            "childporn", "pedo", "pedophile", "underaged", "illegal content", "illegal pics", "illegal images", "illegal videos", "young girl", "young boy",
            "under 18", "underage pics", "underage videos", "minor porn", "teen porn", "teen nudes", "teen sex", "teen cp", "teen loli", "teen jailbait"
        ],
        "crypto": [
            "crypto", "cryptocurrency", "bitcoin", "btc", "eth", "ethereum", "dogecoin", "solana", "airdrop", "wallet", "metamask", "binance", "exchange",
            "token", "coin", "blockchain", "crypto giveaway", "crypto airdrop", "crypto scam", "crypto offer", "crypto rewards", "crypto bonus", "crypto faucet",
            "crypto mining", "crypto investment", "crypto trading", "crypto wallet", "crypto transfer", "crypto hack", "crypto pump", "crypto dump", "crypto free",
            "crypto code", "crypto link", "bitcoin giveaway", "btc giveaway", "eth giveaway", "solana giveaway", "dogecoin giveaway", "metamask airdrop",
            "binance airdrop", "binance bonus", "binance hack", "binance scam", "crypto event", "crypto drop", "crypto promo", "crypto generator", "crypto claim",
            "cryptoapp", "cryptoapp.com", "cryptoscam", "cryptoscam.com"
        ],
        "phishing": [
            "login", "log in", "log-in", "sign in", "sign-in", "signin", "signon", "sign on", "sign-on",
            "verify", "verification", "verified", "verifying", "validate", "validation", "auth", "authenticate", "authentication",
            "password", "passcode", "security code", "security", "secure", "credentials", "account", "account locked", "locked", "unlock",
            "reset", "reset your password", "reset password", "recover", "recovery", "restore", "restore account", "restore access",
            "appeal", "confirm", "confirmation", "confirm your identity", "confirm account", "confirm email", "confirm now",
            "suspicious", "suspicious activity", "unusual activity", "activity detected", "alert", "security alert", "notice", "important notice",
            "update", "update your info", "update info", "update account", "update details",
            "reactivate", "reactivation", "activate", "activation", "deactivate", "deactivation",
            "violation", "terms violation", "policy violation", "breach", "compromised", "compromise",
            "click here", "click the link", "follow this link", "visit this link", "access here", "access your account",
            "instant access", "limited time", "expires soon", "expiring", "urgent", "immediately", "now", "today",
            "log in to your account", "login to your account", "sign in to your account", "verify your account", "account suspended",
            "reset your password", "recover your account", "restore your account", "confirm your identity", "confirm your account", "confirm email",
            "suspicious activity", "security alert", "important notice", "update your info", "update account", "reactivate your account", "activate your account",
            "deactivate your account", "policy violation", "terms violation", "breach detected", "compromised account", "click here to", "click the link below",
            "follow this link", "visit this link", "access your account here", "instant access", "expires soon", "expiring soon", "urgent action required",
            "immediate action required", "discordsecurity", "discord-app", "discord-gift", "discordapp.com/gift", "discord.com/login", "discord.com/verify",
            "discord.com/claim", "discord.com/activate", "discord.com/restore", "discord.com/appeal", "discord.com/confirm", "discord.com/validate",
            "discord.com/secure", "discord.com/alert", "discord.com/notice", "discord.com/verify-account", "discord.com/verifyuser", "discord.com/verifyemail",
            "discord.com/verify-phone", "discord.com/verify-identity", "discord.com/verify-now", "discord.com/verifytoday", "discord.com/verifyme",
            "discord.com/verifyyouraccount", "discord.com/verifyyouridentity", "discord.com/verifyyourself", "discord.com/verifythis", "discord.com/verifyhere",
            "discord.com/verify-link", "discord.com/verify-code", "discord.com/verify-token", "discord.com/verify-password", "discord.com/verify-login",
            "discord.com/verify-reset", "discord.com/verify-security", "discord.com/verify-alert", "discord.com/verify-notice", "discord.com/verify-locked",
            "discord.com/verify-unlock", "discord.com/verify-appeal", "discord.com/verify-confirm", "discord.com/verify-validate", "discord.com/verify-activate",
            "discord.com/verify-authorize", "discord.com/verify-secure", "discord.com/verify-restore", "discord.com/verify-recover", "discord.com/verify-claim",
            "discordsecurity.com", "discordsafe.com", "discordprotect.com", "discord-verify.com", "discord-verification.com", "discordlogin.com",
            "discordreset.com", "discordclaim.com", "discordgift.com", "discordnitro.com", "discordnitro.net", "discordnitro.org", "discordnitro.store",
            "discordnitro.gift", "discordnitro.codes", "discordnitro.online", "discordnitro.site", "discordnitro.xyz", "discordnitro.club", "discordnitro.pro",
            "discordnitro.top", "discordnitro.best", "discordnitro.vip", "discordnitro.today", "discordnitro.app", "discordnitro.page", "discordnitro.space",
            "discordnitro.tech", "discordnitro.shop", "discordnitro.lol", "discordnitro.click", "discordnitro.link", "discordnitro.email", "discordnitro.info",
            "discordnitro.co", "discordnitro.us", "discordnitro.uk", "validate your account", "validate your identity", "validate your email",
            "security verification", "security validation", "security check", "security update", "security notice", "security warning"
        ],
        "roblox": [
            "roblox", "robux", "free robux", "roblox.com", "roblox gift", "roblox code", "roblox promo", "roblox event", "roblox win", "roblox prize",
            "roblox generator", "roblox hack", "roblox exploit", "roblox admin", "roblox staff", "roblox support", "roblox scam", "roblox giveaway",
            "robux generator", "robux hack", "robux giveaway", "robux event", "robux drop", "robux claim", "robux code", "robux promo",
            "roblox.com/games", "roblox.com/gift", "roblox.com/redeem", "roblox.com/event", "roblox.com/win", "roblox.com/prize", "roblox.com/generator",
            "roblox.com/hack", "roblox.com/exploit", "roblox.com/admin", "roblox.com/staff", "roblox.com/support", "roblox.com/scam", "roblox.com/giveaway"
        ],
        "giveaway": [
            "giveaway", "give away", "win", "winner", "winners", "claim your prize", "claim prize", "congratulations", "congrats", "you have won",
            "lucky winner", "lucky winners", "prize", "reward", "rewards", "event", "drop", "airdrop", "loot", "jackpot", "draw", "raffle", "contest",
            "competition", "sweepstakes", "free", "limited time", "exclusive", "special offer", "bonus", "gift", "gifted", "giftbox", "gift box",
            "prize winner", "prize winners", "reward winner", "reward winners", "jackpot winner", "jackpot winners", "airdrop", "loot drop", "lootbox",
            "jackpot", "draw winner", "raffle winner", "contest winner", "competition winner", "sweepstakes winner", "special offer", "exclusive offer",
            "bonus reward", "gifted prize", "giftbox winner", "gift box winner", "limited time offer", "limited time bonus", "exclusive bonus", "special bonus"
        ],
        "adult": [
            "sex", "porn", "xxx", "onlyfans", "only fans", "camgirl", "cam girl", "camgirls", "cam girls", "adult", "escort", "escorts", "18+", "nsfw",
            "hot girls", "hot girl", "sexting", "nude", "nudes", "naked", "erotic", "fetish", "strip", "stripping", "stripper", "strip club", "webcam",
            "web cam", "webcams", "web cams", "snapchat", "snap", "premium", "lewd", "spicy", "sugar daddy", "sugar baby", "sugarbabes", "sugarbabys",
            "snapchat nudes", "snap nudes", "premium nudes", "premium snaps", "premium onlyfans", "premium content", "private nudes", "private snaps"
        ],
        "malware": [
            "exe", ".exe", "scr", ".scr", "bat", ".bat", "com", ".com", "dll", ".dll", "virus", "trojan", "malware", "spyware", "adware", "worm",
            "download this", "infected", "infected file", "infected attachment", "infected link", "infect", "keylogger", "key log", "key loggers", "stealer",
            "steal", "stealing", "hack", "hack tool", "hacked", "hacked client", "hacker", "hacker tool", "crack", "cracked", "cheat", "cheats", "mod menu",
            "modmenu", "injector", "inject", "payload", "exploit", "exploit kit", "exploitkit", "ransomware", "rootkit", "backdoor", "rat",
            "remote access", "remote tool", "remote admin", "remote administration", "remote desktop", "remote access tool", "remote admin tool",
            "remote desktop tool", "phishing", "phishing attachment", "spoof", "spoofed", "spoofing", "spoofed file", "spoofed link", "bypass", "bypasser",
            "bypassing", "patch", "patcher", "patching", "malicious file", "malicious link", "malicious attachment", "malicious download"
        ],
        "giftcard": [
            "gift card", "giftcard", "gift cards", "giftcards", "amazon gift", "amazon card", "itunes gift", "itunes card", "google play gift",
            "google play card", "psn code", "psn card", "xbox code", "xbox card", "gift code", "giftcode", "gift codes", "giftcodes", "voucher",
            "vouchers", "prepaid", "pre-paid", "pre paid", "redeem", "redeem code", "redeem gift", "redeem card", "claim code", "claim gift",
            "amazon gift card", "itunes gift card", "google play gift card", "prepaid card", "pre-paid card", "pre paid card", "redeem gift card",
            "claim gift card", "claim your gift card", "claim your code", "free gift card", "free giftcard", "free gift cards", "free amazon card",
            "free itunes card", "free google play card", "free psn card", "free xbox card"
        ],
        "selfbot": [
            "dm me", "dms open", "direct message me", "add me", "friend me", "private message", "pm me", "message me", "msg me", "contact me",
            "send me a message", "send me dm", "send dm", "slide into my dms", "slide in my dms", "slide in dms", "slide into dms",
            "dm me for", "dms open for", "direct message me for", "private message me for", "pm me for", "message me for", "msg me for", "send me a message for",
            "send me dm for", "send dm for", "slide into my dms for", "slide in my dms for", "slide in dms for", "slide into dms for", "dm me if", "pm me if",
            "message me if", "msg me if", "contact me for", "add me for", "friend me for", "private message for", "open dms for", "open dm for", "open pm for"
        ],
        "other": []
    }

    def __init__(self, bot: commands.Bot) -> None:
        super().__init__()
        self.bot = bot
        # Add all scam types to default stats
        scam_stats_default = {k: 0 for k in self.SCAM_TYPES}
        default_guild = {
            "enabled": False,
            "action": None,
            "logs_channel": None,
            "ping_role": None,
            "honeypot_channel": None,
            "mute_role": None,
            "ban_delete_message_days": 3,
            "scam_stats": scam_stats_default.copy(),
            "honeypot_message_id": None,  # Track the honeypot warning message for reaction triggers
        }
        default_global = {
            "global_scam_stats": scam_stats_default.copy(),
        }
        self.config = Config.get_conf(self, identifier=1234567890, force_registration=True)
        self.config.register_guild(**default_guild)
        self.config.register_global(**default_global)
        self.global_scam_stats = None
        self.bot.loop.create_task(self.initialize_global_scam_stats())
        self.bot.loop.create_task(self.randomize_honeypot_name())
        self.bot.loop.create_task(self.refresh_honeypot_warning_messages())

    async def initialize_global_scam_stats(self):
        self.global_scam_stats = await self.config.global_scam_stats()
        # Ensure all scam types are present
        for scam_type in self.SCAM_TYPES:
            if scam_type not in self.global_scam_stats:
                self.global_scam_stats[scam_type] = 0
        await self.config.global_scam_stats.set(self.global_scam_stats)

    async def randomize_honeypot_name(self):
        await self.bot.wait_until_ready()
        while not self.bot.is_closed():
            for guild in self.bot.guilds:
                config = await self.config.guild(guild).all()
                honeypot_channel_id = config.get("honeypot_channel")
                honeypot_channel = guild.get_channel(honeypot_channel_id) if honeypot_channel_id else None

                if honeypot_channel:
                    dictionary_words = [
                        "level-up", "boss-fight", "loot-box", "quest", "avatar", "guild", "raid", 
                        "dungeon", "pvp", "pve", "respawn", "checkpoint", "leaderboard", "achievement", 
                        "skill-tree", "power-up", "gamepad", "joystick", "console", "arcade", "multiplayer", 
                        "singleplayer", "sandbox", "open-world", "rpg", "fps", "mmo", "strategy", 
                        "simulation", "platformer", "indie", "esports", "tournament", "speedrun", 
                        "modding", "patch", "update", "expansion", "dlc", "beta", "alpha", "early-access", 
                        "game-jam", "pixel-art", "retro", "8-bit", "16-bit", "soundtrack", "cutscene", 
                        "npc", "ai", "game-engine", "physics", "graphics", "rendering", "animation", 
                        "storyline", "narrative", "dialogue", "character-design", "level-design", 
                        "gameplay", "mechanics", "balance", "difficulty", "tutorial", "walkthrough", 
                        "cheat-code", "easter-egg", "glitch", "bug", "patch-notes", "server", "lag", 
                        "ping", "fps-drop", "frame-rate", "resolution", "texture", "shader", "voxel", 
                        "polygon", "vertex", "mesh", "rigging", "skinning", "motion-capture", "voice-acting", 
                        "sound-effects", "ambient-sound", "background-music", "game-theory", "game-design", 
                        "user-interface", "hud", "cross-platform", "cloud-gaming", "streaming", "vr", 
                        "ar", "mixed-reality", "haptic-feedback", "game-economy", "microtransactions", 
                        "in-game-currency", "loot-crate", "battle-pass", "season-pass", "skins", "cosmetics", 
                        "emotes", "dance", "taunt", "clan", "faction", "alliance", "team", "co-op", 
                        "competitive", "ranked", "casual", "hardcore", "permadeath", "roguelike", "metroidvania",
                        "tourist", "sightseeing", "landmark", "itinerary", "excursion", "souvenir", 
                        "travel-guide", "backpacking", "adventure", "resort", "cruise", "destination", 
                        "vacation", "holiday", "tour", "expedition", "journey", "exploration", "getaway",
                        "passport", "visa", "airfare", "luggage", "hostel", "hotel", "motel", "bed-and-breakfast",
                        "road-trip", "car-rental", "flight", "layover", "stopover", "jetlag", "travel-agency",
                        "tour-operator", "safari", "trekking", "hiking", "camping", "beach", "island", 
                        "mountain", "valley", "canyon", "waterfall", "national-park", "wildlife", "culture",
                        "heritage", "festival", "cuisine", "local", "tradition", "custom", "language", 
                        "currency-exchange", "travel-insurance", "backpacker", "globetrotter", "wanderlust",
                        "classroom", "homework", "assignment", "teacher", "student", "principal", "vice-principal", "counselor", "nurse", "janitor",
                        "cafeteria", "lunchbox", "recess", "playground", "blackboard", "whiteboard", "chalk", "marker", "eraser", "desk",
                        "chair", "locker", "hallway", "bell", "schedule", "timetable", "subject", "math", "science", "history",
                        "geography", "english", "literature", "reading", "writing", "spelling", "grammar", "vocabulary", "quiz", "test",
                        "exam", "midterm", "finals", "report-card", "grade", "score", "pass", "fail", "study", "notebook",
                        "textbook", "worksheet", "project", "presentation", "group-work", "partner", "classmate", "friend", "bully", "detention",
                        "library", "librarian", "computer-lab", "science-lab", "experiment", "field-trip", "bus", "uniform", "dress-code", "assembly",
                        "auditorium", "gym", "gymnasium", "coach", "sports", "soccer", "basketball", "baseball", "track", "swimming",
                        "music", "band", "choir", "art", "painting", "drawing", "sculpture", "theater", "drama", "performance",
                        "club", "debate", "student-council", "yearbook", "graduation", "cap-and-gown", "valedictorian", "honor-roll", "scholarship", "tuition"
                    ]
                    random_name = random.choice(dictionary_words)
                    try:
                        await honeypot_channel.edit(name=random_name, reason="Changing channel name to impede honeypot evasion efforts")
                    except discord.HTTPException:
                        pass

            await asyncio.sleep(4 * 60 * 60)  # Wait for 4 hours

    async def refresh_honeypot_warning_messages(self):
        """On cog load, delete the pre-existing honeypot warning message and send a fresh copy. Do this slowly to avoid rate limits."""
        await self.bot.wait_until_ready()
        await asyncio.sleep(10)  # Give a little time for cache to warm up
        for guild in self.bot.guilds:
            try:
                config = await self.config.guild(guild).all()
                honeypot_channel_id = config.get("honeypot_channel")
                if not honeypot_channel_id:
                    continue
                honeypot_channel = guild.get_channel(honeypot_channel_id)
                if not honeypot_channel:
                    continue

                # Try to find the bot's own honeypot warning message (by embed title or image)
                honeypot_message_id = None
                async for msg in honeypot_channel.history(limit=10, oldest_first=True):
                    if (
                        msg.author == guild.me
                        and msg.embeds
                        and (
                            (msg.embeds[0].title and "This channel is a security honeypot" in msg.embeds[0].title)
                            or (msg.embeds[0].image and msg.embeds[0].image.url and "do_not_post_here" in msg.embeds[0].image.url)
                        )
                    ):
                        try:
                            await msg.delete()
                            await asyncio.sleep(2)  # Slow down to avoid rate limits
                        except Exception:
                            pass
                        break  # Only delete one warning message

                # Now send a fresh warning message
                icon_url = None
                if guild.icon:
                    try:
                        icon_url = guild.icon.url
                    except Exception:
                        icon_url = None

                # Determine the configured action for this guild
                action = config.get("action")
                action_descriptions = {
                    "mute": "You will be assigned the server's mute role and lose the ability to speak.",
                    "kick": "You will be kicked from the server immediately.",
                    "ban": "You will be banned from the server immediately.",
                    "timeout": "You will be timed out and unable to interact for 7 days.",
                    None: "Server staff will be notified of your suspicious activity."
                }
                action_text = action_descriptions.get(action, "Server staff will be notified of your suspicious activity.")

                embed = discord.Embed(
                    title="This channel is a security honeypot",
                    description="A honeypot is a cybersecurity mechanism that uses a manufactured (fake) attack target to lure attackers away from legitimate, potentially vulnerable targets. In the same sense, this channel exists solely to bait spam, advertisements, and rule-breaking content from compromised and automated Discord accounts.\n- Real users (accounts not automated or stolen) are able to read the instructions below and follow them.\n- \"Fake\" users (stolen and automated accounts) won't be able to reliably recognize this isn't a real channel and will send messages in it, triggering the honeypot.",
                    color=0xff4545,
                ).add_field(
                    name="What not to do?",
                    value="- **Do not speak in this channel**\n- **Do not send images in this channel**\n- **Do not send files in this channel**\n- **Do not react to this message**",
                    inline=False,
                ).add_field(
                    name="What will happen if I do?",
                    value=action_text,
                    inline=False,
                ).set_footer(text=guild.name, icon_url=icon_url).set_image(url="attachment://do_not_post_here.png").set_thumbnail(url="attachment://stop.png")

                file_path = os.path.join(os.path.dirname(__file__), "do_not_post_here.png")
                stop_file_path = os.path.join(os.path.dirname(__file__), "stop.png")
                files = []
                # Always try to send both images if they exist
                if os.path.isfile(file_path):
                    files.append(discord.File(file_path))
                if os.path.isfile(stop_file_path):
                    files.append(discord.File(stop_file_path))
                try:
                    sent_msg = await honeypot_channel.send(embed=embed, files=files)
                    honeypot_message_id = sent_msg.id
                    await self.config.guild(guild).honeypot_message_id.set(honeypot_message_id)
                    await asyncio.sleep(2)
                except Exception:
                    pass
            except Exception:
                continue
            await asyncio.sleep(2)  # Slow down between guilds

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if not message.guild or message.author.bot:
            return

        config = await self.config.guild(message.guild).all()
        honeypot_channel_id = config.get("honeypot_channel")
        logs_channel_id = config.get("logs_channel")
        logs_channel = message.guild.get_channel(logs_channel_id) if logs_channel_id else None

        if not config["enabled"] or not honeypot_channel_id or not logs_channel or message.channel.id != honeypot_channel_id:
            return

        # Fix: message.guild.me can be None if the bot is not in the guild or cache is not ready
        # Also, top_role can be None if the bot has no roles
        guild_me = message.guild.me
        if not guild_me:
            return

        # Fix: message.author.top_role >= message.guild.me.top_role can raise if top_role is None
        # Also, owner_ids may not be set on all bots, so use getattr with fallback
        owner_ids = getattr(self.bot, "owner_ids", set())
        if (
            message.author.id in owner_ids
            or message.author.guild_permissions.manage_guild
            or (hasattr(message.author, "top_role") and hasattr(guild_me, "top_role") and message.author.top_role >= guild_me.top_role)
        ):
            return

        try:
            await message.delete()
        except discord.HTTPException:
            pass

        # Track scam type based on message content
        scam_type = "other"
        content_lower = message.content.lower()
        for stype, keywords in self.SCAM_TYPES.items():
            if stype == "other":
                continue
            if any(word in content_lower for word in keywords):
                scam_type = stype
                break

        # Update scam stats
        scam_stats = config.get("scam_stats", {})
        # Ensure all scam types are present
        for stype in self.SCAM_TYPES:
            scam_stats.setdefault(stype, 0)
        scam_stats[scam_type] += 1

        # Fix: self.global_scam_stats may not be initialized yet
        if self.global_scam_stats is None:
            self.global_scam_stats = await self.config.global_scam_stats()
        for stype in self.SCAM_TYPES:
            self.global_scam_stats.setdefault(stype, 0)
        self.global_scam_stats[scam_type] += 1

        await self.config.guild(message.guild).scam_stats.set(scam_stats)
        await self.config.global_scam_stats.set(self.global_scam_stats)

        action = config["action"]
        embed = discord.Embed(
            title="Honeypot trap triggered",
            description=f"> {message.content}\n",
            color=0xff4545,
            timestamp=message.created_at,
        )
        embed.add_field(name="User display name", value=message.author.display_name, inline=True)
        embed.add_field(name="User mention", value=message.author.mention, inline=True)
        embed.add_field(name="User ID", value=message.author.id, inline=True)
        embed.add_field(name="Scam type", value=scam_type, inline=True)

        failed = None
        if action:
            try:
                if action == "mute":
                    mute_role_id = config.get("mute_role")
                    mute_role = message.guild.get_role(mute_role_id) if mute_role_id else None
                    if mute_role:
                        await message.author.add_roles(mute_role, reason="User triggered honeypot defenses")
                    else:
                        failed = "**Failed:** The mute role is not set or doesn't exist anymore."
                elif action == "kick":
                    await message.author.kick(reason="User triggered honeypot defenses")
                elif action == "ban":
                    await message.author.ban(reason="User triggered honeypot defenses", delete_message_days=config["ban_delete_message_days"])
                elif action == "timeout":
                    timeout_duration = timedelta(days=7)  # 7 day timeout
                    # Fix: discord.utils.utcnow() is deprecated, use discord.utils.utcnow() if available, else datetime.utcnow
                    try:
                        now = discord.utils.utcnow()
                    except AttributeError:
                        from datetime import datetime, timezone
                        now = datetime.now(timezone.utc)
                    await message.author.edit(timed_out_until=now + timeout_duration, reason="User triggered honeypot defenses")
            except discord.HTTPException as e:
                failed = f"**Failed:** An error occurred while trying to take action against the member:\n{e}"
            except Exception as e:
                failed = f"**Failed:** Unexpected error: {e}"
            else:
                # Log the action (this is a placeholder for actual logging)
                print(f"Action {action} taken against {message.author}")

            action_result = {
                "mute": "I assigned the user the configured mute/suppress role",
                "kick": "The user was kicked from the server",
                "ban": "The user was banned from the server",
                "timeout": "The user was timed out for a week"
            }.get(action, "No action taken.")

            embed.add_field(name="Action taken", value=failed or action_result, inline=False)

        # Fix: message.guild.icon may be None, and .url will raise if so
        icon_url = None
        if message.guild.icon:
            try:
                icon_url = message.guild.icon.url
            except Exception:
                icon_url = None
        embed.set_footer(text=message.guild.name, icon_url=icon_url)
        # Add stop.png as thumbnail if available
        stop_file_path = os.path.join(os.path.dirname(__file__), "stop.png")
        files = []
        if os.path.isfile(stop_file_path):
            embed.set_thumbnail(url="attachment://stop.png")
            files.append(discord.File(stop_file_path))
        ping_role_id = config.get("ping_role")
        ping_role = message.guild.get_role(ping_role_id) if ping_role_id else None
        await logs_channel.send(content=ping_role.mention if ping_role else None, embed=embed, files=files if files else None)

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        # Only trigger if honeypot is enabled and the reaction is on the honeypot warning message
        if not payload.guild_id or not payload.channel_id or not payload.message_id:
            return
        guild = self.bot.get_guild(payload.guild_id)
        if not guild:
            return
        config = await self.config.guild(guild).all()
        if not config.get("enabled"):
            return
        honeypot_channel_id = config.get("honeypot_channel")
        honeypot_message_id = config.get("honeypot_message_id")
        logs_channel_id = config.get("logs_channel")
        if not honeypot_channel_id or not honeypot_message_id or not logs_channel_id:
            return
        if payload.channel_id != honeypot_channel_id or payload.message_id != honeypot_message_id:
            return
        # Ignore bot reactions
        member = guild.get_member(payload.user_id)
        if not member or member.bot:
            return
        # Permission checks (same as on_message)
        guild_me = guild.me
        if not guild_me:
            return
        owner_ids = getattr(self.bot, "owner_ids", set())
        if (
            member.id in owner_ids
            or member.guild_permissions.manage_guild
            or (hasattr(member, "top_role") and hasattr(guild_me, "top_role") and member.top_role >= guild_me.top_role)
        ):
            return

        # Remove the reaction
        channel = guild.get_channel(honeypot_channel_id)
        if channel:
            try:
                msg = await channel.fetch_message(honeypot_message_id)
                await msg.remove_reaction(payload.emoji, member)
            except Exception:
                pass

        # Use "other" as scam type for reactions
        scam_type = "other"
        scam_stats = config.get("scam_stats", {})
        for stype in self.SCAM_TYPES:
            scam_stats.setdefault(stype, 0)
        scam_stats[scam_type] += 1

        if self.global_scam_stats is None:
            self.global_scam_stats = await self.config.global_scam_stats()
        for stype in self.SCAM_TYPES:
            self.global_scam_stats.setdefault(stype, 0)
        self.global_scam_stats[scam_type] += 1

        await self.config.guild(guild).scam_stats.set(scam_stats)
        await self.config.global_scam_stats.set(self.global_scam_stats)

        action = config["action"]
        logs_channel = guild.get_channel(logs_channel_id)
        embed = discord.Embed(
            title="Honeypot trap triggered (reaction)",
            description=f"User reacted to the honeypot warning message.",
            color=0xff4545,
        )
        embed.add_field(name="User display name", value=member.display_name, inline=True)
        embed.add_field(name="User mention", value=member.mention, inline=True)
        embed.add_field(name="User ID", value=member.id, inline=True)
        embed.add_field(name="Scam type", value=scam_type, inline=True)
        failed = None
        if action:
            try:
                if action == "mute":
                    mute_role_id = config.get("mute_role")
                    mute_role = guild.get_role(mute_role_id) if mute_role_id else None
                    if mute_role:
                        await member.add_roles(mute_role, reason="User triggered honeypot defenses (reaction)")
                    else:
                        failed = "**Failed:** The mute role is not set or doesn't exist anymore."
                elif action == "kick":
                    await member.kick(reason="User triggered honeypot defenses (reaction)")
                elif action == "ban":
                    await member.ban(reason="User triggered honeypot defenses (reaction)", delete_message_days=config["ban_delete_message_days"])
                elif action == "timeout":
                    timeout_duration = timedelta(days=7)
                    try:
                        now = discord.utils.utcnow()
                    except AttributeError:
                        from datetime import datetime, timezone
                        now = datetime.now(timezone.utc)
                    await member.edit(timed_out_until=now + timeout_duration, reason="User triggered honeypot defenses (reaction)")
            except discord.HTTPException as e:
                failed = f"**Failed:** An error occurred while trying to take action against the member:\n{e}"
            except Exception as e:
                failed = f"**Failed:** Unexpected error: {e}"
            else:
                print(f"Action {action} taken against {member} (reaction)")
            action_result = {
                "mute": "I assigned the user the configured mute/suppress role",
                "kick": "The user was kicked from the server",
                "ban": "The user was banned from the server",
                "timeout": "The user was timed out for a week"
            }.get(action, "No action taken.")
            embed.add_field(name="Action taken", value=failed or action_result, inline=False)
        icon_url = None
        if guild.icon:
            try:
                icon_url = guild.icon.url
            except Exception:
                icon_url = None
        embed.set_footer(text=guild.name, icon_url=icon_url)
        # Add stop.png as thumbnail if available
        stop_file_path = os.path.join(os.path.dirname(__file__), "stop.png")
        files = []
        if os.path.isfile(stop_file_path):
            embed.set_thumbnail(url="attachment://stop.png")
            files.append(discord.File(stop_file_path))
        ping_role_id = config.get("ping_role")
        ping_role = guild.get_role(ping_role_id) if ping_role_id else None
        if logs_channel:
            await logs_channel.send(content=ping_role.mention if ping_role else None, embed=embed, files=files if files else None)

    @commands.guild_only()
    @commands.admin_or_permissions()
    @commands.group()
    async def honeypot(self, ctx: commands.Context) -> None:
        """Honeypots are channels that attract advertising bots and compromised Discord accounts to detect and remove them from your server before they can hurt you or your members."""
        pass

    @commands.admin_or_permissions()
    @honeypot.command()
    async def create(self, ctx: commands.Context) -> None:
        """Create the honeypot channel."""
        async with ctx.typing():
            honeypot_channel_id = await self.config.guild(ctx.guild).honeypot_channel()
            honeypot_channel = ctx.guild.get_channel(honeypot_channel_id) if honeypot_channel_id else None

            if honeypot_channel:
                embed = discord.Embed(
                    title="Honeypot channel exists",
                    description=f"The honeypot channel already exists: {honeypot_channel.mention} ({honeypot_channel.id}).",
                    color=0xff4545
                )
                await ctx.send(embed=embed)
                return

            # Fix: If the bot does not have permission to create channels at position 0, fallback to default
            try:
                honeypot_channel = await ctx.guild.create_text_channel(
                    name="honeypot",
                    position=0,
                    overwrites={
                        ctx.guild.me: discord.PermissionOverwrite(
                            view_channel=True,
                            read_messages=True,
                            send_messages=True,
                            manage_messages=True,
                            manage_channels=True,
                        ),
                        ctx.guild.default_role: discord.PermissionOverwrite(
                            view_channel=True, read_messages=True, send_messages=True
                        ),
                    },
                    reason=f"Honeypot channel creation requested by {ctx.author.display_name} ({ctx.author.id}).",
                )
            except Exception as e:
                embed = discord.Embed(
                    title="Failed to create honeypot channel",
                    description=f"An error occurred: {e}",
                    color=0xff4545
                )
                await ctx.send(embed=embed)
                return

            # Fix: ctx.guild.icon may be None
            icon_url = None
            if ctx.guild.icon:
                try:
                    icon_url = ctx.guild.icon.url
                except Exception:
                    icon_url = None

            # Determine the configured action for this guild
            config = await self.config.guild(ctx.guild).all()
            action = config.get("action")
            action_descriptions = {
                "mute": "You will be assigned the server's suppression role and lose the ability to speak in this server until a staff member removes the role.",
                "kick": "You will be kicked from the server immediately.",
                "ban": "You will be banned from the server immediately.",
                "timeout": "You will be timed out and unable to interact with text or voice channels for 7 days.",
                None: "Server staff will be notified of your suspicious activity."
            }
            action_text = action_descriptions.get(action, "Server staff will be notified of your suspicious activity.")

            embed = discord.Embed(
                title="This channel is a security honeypot",
                description="A honeypot is a cybersecurity mechanism that uses a manufactured (fake) attack target to lure attackers away from legitimate, potentially vulnerable targets. In the same sense, this channel exists solely to bait spam, advertisements, and rule-breaking content from compromised and automated Discord accounts.\n- Real users (accounts not automated or stolen) are able to read the instructions below and follow them.\n- \"Fake\" users (stolen and automated accounts) won't be able to reliably recognize this isn't a real channel and will send messages in it, triggering the honeypot.",
                color=0xff4545,
            ).add_field(
                name="What not to do?",
                value="- **Do not speak in this channel**\n- **Do not send images in this channel**\n- **Do not send files in this channel**\n- **Do not react to this message**",
                inline=False,
            ).add_field(
                name="What will happen if I do?",
                value=action_text,
                inline=False,
            ).set_footer(text=ctx.guild.name, icon_url=icon_url).set_image(url="attachment://do_not_post_here.png").set_thumbnail(url="attachment://stop.png")

            # Fix: File may not exist, so catch error
            file_path = os.path.join(os.path.dirname(__file__), "do_not_post_here.png")
            stop_file_path = os.path.join(os.path.dirname(__file__), "stop.png")
            files = []
            if os.path.isfile(file_path):
                files.append(discord.File(file_path))
            if os.path.isfile(stop_file_path):
                files.append(discord.File(stop_file_path))
            if not files:
                # Optionally, warn the user
                await ctx.send("Warning: Neither 'do_not_post_here.png' nor 'stop.png' was found. The honeypot channel will be created without the images.")

            sent_msg = await honeypot_channel.send(
                embed=embed,
                files=files,
            )
            await self.config.guild(ctx.guild).honeypot_channel.set(honeypot_channel.id)
            await self.config.guild(ctx.guild).honeypot_message_id.set(sent_msg.id)
            embed = discord.Embed(
                title="Honeypot created",
                description=(
                    f"The honeypot has been created - {honeypot_channel.mention} ({honeypot_channel.id}).\n"
                    "Make sure to activate it after configuring a logging channel and punishment action\n- `honeypot activate`"
                ),
                color=0x2bbd8e
            )
            await ctx.send(embed=embed)

    @commands.admin_or_permissions()
    @honeypot.command()
    async def activate(self, ctx: commands.Context) -> None:
        """Enable the honeypot functionality."""
        async with ctx.typing():
            await self.config.guild(ctx.guild).enabled.set(True)
            embed = discord.Embed(
                title="Honeypot enabled",
                description="Honeypot functionality has been enabled.",
                color=0x2bbd8e
            )
            await ctx.send(embed=embed)

    @commands.admin_or_permissions()
    @honeypot.command()
    async def disable(self, ctx: commands.Context) -> None:
        """Disable the honeypot functionality."""
        async with ctx.typing():
            await self.config.guild(ctx.guild).enabled.set(False)
            embed = discord.Embed(
                title="Honeypot disabled",
                description="Honeypot functionality has been disabled.",
                color=0xff4545
            )
            await ctx.send(embed=embed)

    @commands.admin_or_permissions()
    @honeypot.command()
    async def remove(self, ctx: commands.Context) -> None:
        """Disable the honeypot and delete the honeypot channel."""
        async with ctx.typing():
            honeypot_channel_id = await self.config.guild(ctx.guild).honeypot_channel()
            honeypot_channel = ctx.guild.get_channel(honeypot_channel_id) if honeypot_channel_id else None

            if honeypot_channel:
                try:
                    await honeypot_channel.delete(reason=f"Honeypot channel removal requested by {ctx.author.display_name} ({ctx.author.id}).")
                except Exception as e:
                    embed = discord.Embed(
                        title="Failed to delete honeypot channel",
                        description=f"An error occurred: {e}",
                        color=0xff4545
                    )
                    await ctx.send(embed=embed)
                    # Still clear config and disable
                await self.config.guild(ctx.guild).honeypot_channel.set(None)
                await self.config.guild(ctx.guild).honeypot_message_id.set(None)
                embed = discord.Embed(
                    title="Honeypot channel removed",
                    description="Honeypot channel has been deleted and configuration cleared.",
                    color=0xff4545
                )
                await ctx.send(embed=embed)
            else:
                embed = discord.Embed(
                    title="No honeypot channel",
                    description="No honeypot channel to delete.",
                    color=0xff4545
                )
                await ctx.send(embed=embed)

            await self.config.guild(ctx.guild).enabled.set(False)

    @commands.admin_or_permissions()
    @honeypot.command()
    async def action(self, ctx: commands.Context, action: str) -> None:
        """Set the action to take when a user is detected in the honeypot channel."""
        async with ctx.typing():
            if action not in ["mute", "kick", "ban", "timeout"]:
                embed = discord.Embed(
                    title="Invalid action",
                    description="Invalid action. Please choose from: mute, kick, ban, timeout.",
                    color=0xff4545
                )
                await ctx.send(embed=embed)
                return
            await self.config.guild(ctx.guild).action.set(action)
            embed = discord.Embed(
                title="Action set",
                description=f"Action has been set to {action}.",
                color=0x2bbd8e
            )
            await ctx.send(embed=embed)

    @commands.admin_or_permissions()
    @honeypot.command()
    async def logs(self, ctx: commands.Context, channel: discord.TextChannel) -> None:
        """Set the channel where logs will be sent."""
        async with ctx.typing():
            await self.config.guild(ctx.guild).logs_channel.set(channel.id)
            embed = discord.Embed(
                title="Logs set",
                description=f"Logs channel has been set to {channel.mention}.",
                color=0x2bbd8e
            )
            await ctx.send(embed=embed)

    @commands.admin_or_permissions()
    @honeypot.command()
    async def settings(self, ctx: commands.Context) -> None:
        """View the current honeypot settings."""
        async with ctx.typing():
            config = await self.config.guild(ctx.guild).all()
            embed = discord.Embed(title="Current honeypot settings", color=0xfffffe)
            embed.add_field(name="Enabled", value=config.get("enabled", False), inline=False)
            embed.add_field(name="Action", value=config.get("action") or "Not set", inline=False)
            logs_channel_id = config.get("logs_channel")
            ping_role_id = config.get("ping_role")
            honeypot_channel_id = config.get("honeypot_channel")
            honeypot_message_id = config.get("honeypot_message_id")
            mute_role_id = config.get("mute_role")
            embed.add_field(name="Logs channel", value=f"<#{logs_channel_id}>" if logs_channel_id else "Not set", inline=False)
            embed.add_field(name="Ping role", value=f"<@&{ping_role_id}>" if ping_role_id else "Not set", inline=False)
            embed.add_field(name="Honeypot channel", value=f"<#{honeypot_channel_id}>" if honeypot_channel_id else "Not set", inline=False)
            embed.add_field(name="Honeypot message ID", value=honeypot_message_id or "Not set", inline=False)
            embed.add_field(name="Mute role", value=f"<@&{mute_role_id}>" if mute_role_id else "Not set", inline=False)
            embed.add_field(name="Days to delete on ban", value=config.get("ban_delete_message_days", 3), inline=False)
            await ctx.send(embed=embed)

    @honeypot.command()
    async def stats(self, ctx: commands.Context) -> None:
        """View the current honeypot statistics."""
        async with ctx.typing():
            config = await self.config.guild(ctx.guild).all()
            global_stats = await self.config.global_scam_stats()
            scam_stats = config.get('scam_stats', {})
            for stype in self.SCAM_TYPES:
                scam_stats.setdefault(stype, 0)
                global_stats.setdefault(stype, 0)

            # Prepare server stats lines
            server_lines = []
            for stype in self.SCAM_TYPES:
                if stype == "other":
                    server_lines.append(f"**Uncategorized detections:** {scam_stats.get(stype, 0)}")
                else:
                    pretty = stype.replace("_", " ").capitalize()
                    server_lines.append(f"**{pretty} flags:** {scam_stats.get(stype, 0)}")

            # Prepare global stats lines
            global_lines = []
            for stype in self.SCAM_TYPES:
                if stype == "other":
                    global_lines.append(f"**Uncategorized detections:** {global_stats.get(stype, 0)}")
                else:
                    pretty = stype.replace("_", " ").capitalize()
                    global_lines.append(f"**{pretty} flags:** {global_stats.get(stype, 0)}")

            embed = discord.Embed(title="Honeypot detection statistics", color=0xfffffe)
            embed.add_field(
                name="In this server",
                value="\n".join(server_lines) or "No detections.",
                inline=False
            )
            embed.add_field(
                name="In all servers",
                value="\n".join(global_lines) or "No detections.",
                inline=False
            )

            await ctx.send(embed=embed)
