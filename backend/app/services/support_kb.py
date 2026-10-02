"""Static troubleshooting knowledge base for the Support assistant. No AI and no provider is involved: this is plain data that
services/support.py matches against with simple keyword scoring. Edit the text here; nothing else needs to change.

Each entry: id, category, title, keywords (phrases score more than single words), steps, follow_up, feature (the feature switch the
topic belongs to, so a switched-off feature is described as unavailable instead of broken).
Placeholders filled at answer time from the real configuration/usage: {plans}, {usage}."""
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Entry:
    id: str
    category: str
    title: str
    keywords: tuple[str, ...]
    steps: tuple[str, ...]
    follow_up: str = "Did this solve the problem?"
    feature: str | None = None          # a feature-switch id (services/features.py), e.g. "video", "face_swap"
    intro: bool = False                 # shown when the user picks the category from the quick buttons
    escalate: bool = True               # offer the Yes/Still-not-working buttons


CATEGORIES: dict[str, str] = {
    "AUTH": "Login and account", "WRITE": "Write, Story and Script", "VIDEO": "Video", "IMAGE": "Image", "MUSIC": "Music", "VOICE": "Voice and audio",
    "LYRICS": "Lyrics", "PROJECT": "Projects and downloads", "PAYMENT": "Payments and plans", "USAGE": "Usage limits", "UPLOAD": "Uploads", "GENERAL": "Something else",
}

# Quick buttons shown when Support opens: (label, category)
QUICK_ACTIONS: tuple[tuple[str, str], ...] = (
    ("Generation problem", "VIDEO"), ("Login problem", "AUTH"), ("Payment problem", "PAYMENT"), ("Upload problem", "UPLOAD"),
    ("Write / Story / Script", "WRITE"), ("Something else", "GENERAL"),
)

# Topics that exist in the code but are switched off by default. Asking about one while it is off gets "unavailable", never a repair guide.
DISABLED_TOPICS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("face_swap", ("face swap", "faceswap", "face replacement", "swap face", "swap my face", "replace face")),
    ("ai_avatar", ("ai avatar", "talking avatar", "avatar video")),
    ("interactive_avatar", ("interactive avatar", "talk to avatar", "chat with avatar")),
    ("hindi", ("hindi",)),
    ("telugu", ("telugu",)),
    ("ai_avatar", ("avatar",)),
)

ENTRIES: tuple[Entry, ...] = (
    # ------------------------------------------------------------------ AUTH
    Entry("auth_google", "AUTH", "Google sign-in", ("google login", "google sign in", "sign in with google", "continue with google", "google button", "google account"),
          ("Make sure pop-ups and third-party cookies are not blocked for this site.", "Try again in a private/incognito window.",
           "If you originally signed up with email and password, sign in with that instead.", "If Google sign-in is not shown on the login page, it is not available right now; use email and password.")),
    Entry("auth_password", "AUTH", "Password problems", ("forgot password","forgot my password", "i forgot my password",  "reset password", "password not working", "wrong password", "password reset", "change password", "invalid password", "incorrect password"),
          ("Use \"Forgot password?\" on the login page to get a reset link by email.", "Check your spam folder and wait a minute for the email.",
           "Make sure you are using the same email address you signed up with.", "Reset links expire, so use the newest one.")),
    Entry("auth_verify", "AUTH", "Email verification code", ("verification code", "verify email", "otp", "code not received", "no email", "confirmation code", "verify my email"),
          ("Check your spam/junk folder for the code.", "Wait about a minute and request a new code; only the newest code works.",
           "Make sure the email address is spelled correctly.", "Codes expire after a few minutes, so enter it promptly.")),
    Entry("auth_session", "AUTH", "Signed out or session expired", ("session expired", "logged out", "signed out", "keep logging out", "unauthorized", "please sign in", "401", "not signed in", "sign in again"),
          ("Sign out, then sign in again.", "Make sure your browser allows site data (cookies/local storage) for this site.", "Avoid using several accounts in different tabs at the same time.",
           "If it keeps happening, tell me which page you were on.")),
    Entry("auth_login", "AUTH", "Can't sign in", ("login", "log in", "sign in", "cant login", "cant log in", "cannot login", "unable to login", "account", "signin", "login failed", "invalid credentials", "register", "sign up", "signup"),
          ("Check the email address and password for typos (passwords are case-sensitive).", "Use \"Forgot password?\" if you are unsure of the password.",
           "Refresh the page and try again.", "If your account was disabled you will see a message saying so; in that case contact the admin team.", "Try another browser or a private window."),
          intro=True),
    # ------------------------------------------------------------------ WRITE
    Entry("write_page", "WRITE", "The Write page", ("write page", "write not opening", "write page not opening", "write not working", "write tab", "write button", "open write", "write page isnt working"),
          ("Refresh the page.", "Sign out and sign back in.", "Try opening Write from the Create page.", "If the page still shows an error, send this issue to the admin team and include what you saw."),
          feature="story"),
    Entry("write_failed", "WRITE", "Story or script generation failed", ("story generation failed", "script generation failed","story is not generating", "script is not generating", "story isnt generating", "script isnt generating",  "story failed", "script failed", "story not generating", "script not generating", "generate story",
                                                                          "generate script", "story", "script", "screenplay", "text provider", "convert story to script"),
          ("Wait a minute and try again: the text AI is sometimes busy, and DreamCast already retries automatically.", "Shorten your idea a little and remove unusual characters.",
           "Check Usage to make sure you have story/script generations left this month.", "If it says the text provider is not configured, the feature is not set up yet; the admin team can fix that."),
          feature="story", intro=True),
    Entry("write_refine", "WRITE", "Prompt refinement", ("refinement", "refine", "prompt refinement", "basic refinement", "ai refined", "refined prompt", "refine prompt", "refining"),
          ("\"AI refined\" means the AI rewrote your prompt. \"Basic refinement\" is a built-in, non-AI template used when the AI is unavailable or switched off.",
           "Either one is fine: you can edit the refined prompt before generating.", "You are never blocked from generating just because refinement is unavailable.")),
    Entry("write_lost", "WRITE", "Story or script not saved", ("story not saved", "script not saved", "lost my story", "lost my script", "save story", "save script", "where is my story", "where is my script"),
          ("Choose a project in \"Save to a project\" before generating; otherwise the result is only shown on the page.", "Saved stories and scripts are in the project's Story and Script tabs.",
           "Copy the text you want to keep before leaving the page.")),
    # ------------------------------------------------------------------ VIDEO
    Entry("video_stuck", "VIDEO", "Video stuck or slow", ("video stuck", "stuck","video is not generating", "video isnt generating", "video wont generate",  "video not generating", "video taking long", "still processing", "video processing", "queued", "video slow", "video pending", "video loading", "generating forever"),
          ("Videos can take a few minutes. Open History to see the status: Queued, Processing, Completed or Failed.", "You can leave the page; the video is created in the background and appears in your project.",
           "If it has been Processing for more than about 15 minutes, it should fail on its own; then try again.", "Avoid starting many videos at once."),
          feature="video"),
    Entry("video_failed", "VIDEO", "Video failed", ("video failed", "video generation failed", "generation failed", "video error", "failed to generate video", "video not working", "video", "videos", "generate video", "text to video", "image to video"),
          ("Open History and read the message on the failed video; it says what went wrong.", "Try a simpler prompt and the default duration and aspect ratio.",
           "A failed video that never reached the provider does not use your monthly allowance.", "Wait a few minutes and try again if the message says the provider is busy."),
          feature="video", intro=True),
    Entry("video_unavailable", "VIDEO", "Provider unavailable", ("provider unavailable", "provider is unavailable", "provider not configured", "isnt set up", "temporarily overloaded", "service unavailable", "503", "provider busy", "unavailable"),
          ("This usually means the AI service behind the feature is busy or not set up. DreamCast retries automatically before showing it.", "Wait a minute or two and try again.",
           "If it stays this way for a long time, send it to the admin team so they can check the provider.")),
    Entry("video_nothing", "VIDEO", "Clicked Generate but nothing happened", ("nothing happened", "clicked generate", "generate button", "button not working", "generate not working", "nothing happens", "no response", "click generate"),
          ("Check that you refined the prompt first: Generate is enabled after the refined prompt appears.", "Look for a red or yellow message under the prompt; it says what is missing (for example a project or a reference image).",
           "Refresh the page and try once more.", "Check Usage in case your monthly allowance is used up.")),
    # ------------------------------------------------------------------ IMAGE
    Entry("image_failed", "IMAGE", "Image generation problems", ("image generation", "image failed","image is not generating", "image isnt generating",  "image not generating", "generate image", "image error", "image unavailable", "images", "picture", "image"),
          ("Open History and read the message on the failed image.", "Try a shorter, simpler description and the 1:1 aspect ratio.", "Wait a minute and try again if the provider was busy.",
           "Check Usage to see how many image generations are left this month."), feature="image", intro=True),
    # ------------------------------------------------------------------ MUSIC
    Entry("music_failed", "MUSIC", "Music generation problems", ("music generation", "music failed","music is not generating", "music isnt generating",  "music not generating", "generate music", "music", "song", "soundtrack", "instrumental"),
          ("Music is created in the background; check History for Queued, Processing or Completed.", "Try the 10 second duration first; it is quickest.",
           "The first request can be slow while the music model wakes up; try again after a minute."), feature="music", intro=True),
    Entry("music_vocals", "MUSIC", "Vocals in music", ("vocals", "singing", "sing", "instrumental vocals", "with vocals", "lyrics in music", "voice in music"),
          ("Music is Instrumental by default.", "\"Instrumental + Vocals\" only works when the music service supports singing; if it does not, you will see a message saying so and nothing is generated.",
           "For spoken words, use the Voice generator instead."), feature="music"),
    # ------------------------------------------------------------------ VOICE
    Entry("voice_silent", "VOICE", "Audio has no sound", ("no sound", "audio has no sound", "silent", "cant hear", "no audio", "audio not playing", "audio not working", "sound not working", "mute", "volume"),
          ("Check your device volume and that the browser tab is not muted.", "Try the Download button and play the file in another app.", "Try another browser if it still plays silently.",
           "If the file itself is silent or very short, generate it again.")),
    Entry("voice_failed", "VOICE", "Voice generation problems", ("voice generation", "voice failed","voice is not generating", "voice isnt generating",  "voice not generating", "generate voice", "voice", "speech", "text to speech", "tts", "audio", "narration"),
          ("Voice reads your text exactly as written: keep it under the length limit shown on the page.", "Pick a Gender and Emotion; if one is not supported you will see a note about it.",
           "Wait a minute and try again if the voice service was busy.", "If it says the voice provider is not configured, the feature is not set up yet."), feature="voice", intro=True),
    Entry("voice_options", "VOICE", "Male / female voice and emotion", ("male voice", "female voice", "emotion", "gender", "angry voice", "sad voice", "happy voice", "voice emotion", "accent"),
          ("Voice offers Male and Female, and the emotions Neutral, Happy, Sad, Angry, Excited, Calm, Fearful and Serious.", "Emotion is approximated with speaking speed and pitch, so the effect is subtle for some voices.",
           "Only English accents are offered unless more languages have been enabled."), feature="voice"),
    Entry("voice_download", "VOICE", "Downloading audio", ("audio download", "download audio", "download voice", "download music", "download my audio", "cant download audio"),
          ("Open the audio from your project or History and use the Download button.", "If the browser opens a player instead, use its three-dot menu → Download.", "Make sure you are still signed in; downloads need your session.")),
    # ------------------------------------------------------------------ LYRICS
    Entry("lyrics", "LYRICS", "Lyrics generation", ("lyrics", "lyric", "lyrics generation", "song lyrics", "lyrics failed", "generate lyrics"),
          ("Describe the theme and mood of the song; longer ideas give better lyrics.", "Lyrics are generated in the background; check History for the status.", "You can edit the lyrics before saving them to a project.",
           "Wait a minute and try again if the text AI was busy."), feature="lyrics", intro=True),
    # ------------------------------------------------------------------ PROJECT
    Entry("project_loading", "PROJECT", "Project not loading", ("project not loading", "project wont open", "project not opening", "cant open project", "project blank", "project missing", "project not found", "my project"),
          ("Refresh the page.", "Make sure you are signed in to the account that created the project: projects are private to their owner.", "Open Projects from the sidebar and select it from the list.",
           "If the project has disappeared from the list, tell me when you last saw it."), intro=True),
    Entry("project_assets", "PROJECT", "Saving, assets and downloads", ("save", "saved", "download", "asset", "assets", "library", "cant download", "download failed", "where is my video", "where is my image", "missing asset"),
          ("Results are saved to the project you chose; with no project they go to \"Quick creations\".", "Everything you generated is also listed in Library and History.", "Use the Download button on the asset's page.",
           "Existing assets stay available even when a generator is switched off.")),
    # ------------------------------------------------------------------ PAYMENT
    Entry("payment_failed", "PAYMENT", "Payment failed or not applied", ("payment failed", "payment not working", "payment", "razorpay", "paid but", "money deducted", "charged", "payment pending", "payment error", "checkout", "upgrade failed", "transaction"),
          ("Payments run in test mode right now: use Razorpay's test card details, not a real card.", "Wait a minute and refresh the Plans page; your plan updates once the payment is confirmed.",
           "Do not pay twice: if a payment shows as pending, check Plans before trying again.", "If you were charged and the plan did not change, send this to the admin team and include the time of the payment."), intro=True),
    Entry("payment_plans", "PAYMENT", "Plans and upgrading", ("plan", "plans", "subscription", "upgrade", "teaser", "trailer", "movie plan", "price", "pricing", "cost", "how much", "cancel subscription", "renew"),
          ("Here are the plans: {plans}.", "Open Plans from the sidebar to upgrade; payments are in test mode.", "A paid plan lasts 30 days from the payment; there is no automatic renewal.", "Your current plan and usage are shown under Plans and Usage.")),
    # ------------------------------------------------------------------ USAGE
    Entry("usage_limit", "USAGE", "Limits and remaining generations", ("limit", "monthly limit", "videos remaining", "credits", "limit reached", "quota", "allowance", "how many videos", "remaining", "out of videos", "used all", "no credits", "usage"),
          ("{usage}", "Allowances are per month and reset at the start of each month (UTC). The plans: {plans}.", "Open Usage to see every generator's allowance.", "Upgrading on Plans raises the allowance immediately.", "Failed generations that never reached the provider are not counted.")),
    # ------------------------------------------------------------------ UPLOAD
    Entry("upload_failed", "UPLOAD", "Upload problems", ("upload", "uploading", "cant upload", "upload failed", "image upload", "video upload", "file upload", "file too large", "file", "reference image", "wrong file type", "unsupported file"),
          ("Use a JPG, PNG or WebP image (or MP4/WebM video where videos are accepted).", "Check the size limit shown next to the upload box; very large files are refused.",
           "Make sure you have a project open: uploads belong to a project.", "Try a smaller file or another browser, and refresh before trying again."), intro=True),
    # ------------------------------------------------------------------ GENERAL
    Entry("general_blank", "GENERAL", "Blank or broken page", ("blank page", "white screen", "page not loading", "wont load", "not loading", "page is blank", "loading forever", "spinner", "stuck loading", "page crashed", "crash"),
          ("Refresh the page (Ctrl/Cmd + Shift + R for a hard refresh).", "Sign out and sign back in.", "Try another browser or a private window.", "Check your internet connection.", "If it keeps happening, send this issue to the admin team with the page name.")),
    Entry("general_error", "GENERAL", "Something went wrong", ("something went wrong", "error", "problem", "issue", "not working", "bug", "failed", "broken", "doesnt work", "cant", "wont work"),
          ("Refresh the page and try again.", "Sign out and sign back in.", "Note the exact message shown on the screen; it tells us what failed.", "If it keeps happening, send this issue to the admin team."), intro=True),
)
