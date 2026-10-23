"""Reviewed, versioned help for the actual Members Portal screens. No account data."""
def guide(slug,title,category,intro,path,image,steps,note=''):
    return dict(slug=slug,title=title,category=category,intro=intro,path=path,image=image,
                steps=[dict(title=t,text=d) for t,d in steps],note=note)

MEMBER=[
guide('join-and-sign-in','Join Members Portal and sign in','Getting started','Accept your invitation, verify your email and start using your membership.','/member','join',[
('Open your invitation','Use the invitation link supplied by your administrator. An emailed invitation belongs to its named recipient; expired or revoked links need a replacement.'),
('Create your account','Choose your username and a password of 12–256 characters. Enter your email, confirm the password and agree to account creation before submitting.'),
('Verify your email','Open the verification email and confirm your address. If it is missing, check junk mail, then use Resend verification email on My membership.'),
('Complete any payment','A paid invitation requires its monthly subscription payment. Free invitations do not need checkout. Your linked service accounts are set up automatically. When a new Emby Connect account is created for you, it uses the same email and password you chose when joining this site. Existing Connect accounts keep their own sign-in details.'),
('Sign in next time','Use your registered email and password on the sign-in page. If you enabled MFA, also enter your authenticator code. Open Linked accounts to check setup progress.')], 'Use your registered membership email, not a different address you use elsewhere.'),
guide('membership-and-payments','Pay for your membership or retry a payment','Membership','Understand your status and use the payment option available for your account.','/member','membership',[
('Open My membership','The membership card shows Active, Waiting for subscription payment, or Payment failed. Free accounts say Your account is on the house and require no payment.'),
('Check your plan','For paid membership, review the plan name, monthly price and any paid-through date. Verify your email first if the page asks you to.'),
('Pay securely','Choose Pay subscription with Stripe or PayPal when offered. Complete the provider’s secure checkout. For an existing unpaid Stripe subscription, use Complete payment / billing details instead.'),
('Retry after a failure','If Payment failed is shown, use Retry payment with the available provider. Automatic collection has stopped; a failed renewal does not add paid time. Existing paid time remains available until its end.'),
('Confirm access','Return to My membership and refresh after payment confirmation. PayPal approval alone is not a completed payment. If a plan has no payment option, contact your administrator.')]),
guide('cancel-subscription','Cancel your subscription renewal','Membership','Stop future renewals while keeping access for time already paid for.','/member','membership',[
('Open My membership','Check that you are viewing the correct paid membership and note your paid-through date.'),
('Choose Cancel subscription','Active renewing subscriptions show a cancellation button. Free accounts do not have a subscription to cancel. If Renewal is cancelled is already shown, cancellation is already recorded.'),
('Read the confirmation','The site confirms that renewal has stopped. This is not account deletion and does not immediately remove the paid time you already have.'),
('Check your end date','You can continue using eligible services until your paid-through date. Billing details & invoices opens Stripe billing when linked. To permanently remove the account, use the separate deletion guide.')]),
guide('request-movies-and-tv','Request movies and TV shows','Requests','Find a title and send a request using your own Ombi account.','/requests','requests',[
('Open Discover & request','You need an active, verified membership. Your Ombi request account links automatically; if setup is pending, check Linked accounts.'),
('Search for a title','Enter a title in Search, select Movies or Television, then select Search.'),
('Choose the matching result','Read the title and description. Available means the item is ready, while Already requested means another request is already recorded.'),
('Send your request','Select Request movie or Request all seasons. TV requests from this screen request all seasons; it is not a single-episode selector. Ombi permissions and request limits still apply.'),
('Follow its status','Return to My membership and scroll to My movie & TV requests. Your request appears there when Ombi records it.')]),
guide('track-requests','Check the status of your requests','Requests','See your own movie and TV requests underneath your membership.','/member#my-requests','history',[
('Find My movie & TV requests','Open My membership and scroll below the membership card. The list belongs to your linked request account, not other members.'),
('Read Pending approval','The request has not yet been approved. Wait for the administrator’s review; repeated requests do not speed this up.'),
('Read Approved — queued / processing','Approval is recorded, but the title is not yet marked available. It may be waiting for a download or processing. Live downloading percentages are not supplied here.'),
('Read Available or Declined','Available means Ombi has marked the item ready. Declined means it was not approved; contact your administrator if you need clarification.'),
('Refresh when needed','Reload the page for the latest status. If request history is temporarily unavailable, try again later; your membership details and payment controls remain available.')]),
guide('linked-accounts','Find your linked accounts and update your username','Accounts','Check Emby and Ombi setup without creating duplicate accounts.','/linked-accounts','linked',[
('Open Linked accounts','Use the sidebar tab. The Emby table lists each assigned server, username, setup state, Connect state and access status.'),
('Check setup and access','Enabled means that server account can be used subject to its permissions. Setup pending or an error needs completion or administrator attention. Unpaid membership does not become active just because an account exists.'),
('Recheck Connect links','If you completed Emby’s own verification, select Recheck pending Connect links. Automatically created Emby Connect accounts use the same email and password you chose when joining this site. Existing Connect accounts keep their own details. Emby Connect password changes are managed separately from this site.'),
('Change your shared username','Enter the new username under One username across your servers, confirm your current portal password and select Save & sync username.'),
('Check the result','The portal and linked Emby server names are synchronized. Global Emby Connect and existing Ombi identities are not renamed. Ask your administrator about persistent sync errors.')]),
guide('passwords','Change or reset your password','Security','Update your sign-in password and understand which linked services synchronize.','/security','security',[
('Change a password you know','Open Security & MFA. Under Change password, enter your current password, the new password and its confirmation, then select Update password.'),
('Recover a forgotten password','From the sign-in page, choose the forgotten-password link and enter your registered email. Open the reset email and submit the new matching password. Requesting the email alone changes nothing.'),
('Check linked-service syncing','Completed changes and resets automatically queue updates for eligible linked Emby servers and Ombi. Security & MFA shows progress or a retry message; a provider outage can delay synchronization.'),
('Change Emby Connect separately','An automatically created Emby Connect account starts with the same email and password you chose when joining this site. Later changes to your portal password do not change your global Emby Connect password, which is managed by Emby. Use the Emby Connect link on Security & MFA to change it separately.'),
('Sign in with the new password','Other portal sessions are signed out. MFA stays enabled. If a sync expires, change your password again to restart it or contact your administrator.')]),
guide('two-factor-authentication','Protect your account with MFA','Security','Add a six-digit authenticator code after your password when signing in.','/security','security',[
('Open Security & MFA','Find Two-factor authentication (MFA). A code from an authenticator app adds protection if someone learns your password.'),
('Start setup','Enter your current password and select Set up authenticator. Use an authenticator app such as Microsoft Authenticator, Google Authenticator or 1Password.'),
('Scan and confirm','Scan the QR code, or enter the displayed setup key in your authenticator. Enter your current password and a fresh six-digit code, then select Enable MFA.'),
('Save your recovery codes','Store the one-time recovery codes somewhere private, separate from your password. They are displayed once and can help if you lose your authenticator.'),
('Use MFA when signing in','After your password, enter the current code or an unused recovery code. Members can disable MFA with their password and a valid factor; administrators must keep MFA enabled.')], 'Never share your QR code, setup key, authenticator code or recovery codes.'),
guide('delete-account','Permanently delete your account','Accounts','Understand permanent removal before choosing it instead of cancellation.','/security','delete',[
('Decide whether you only want to cancel','If you only want to stop future charges, cancel renewal from My membership. Account deletion is permanent and is a different action.'),
('Open the deletion page','In Security & MFA, select Delete my account. Read the explanation before continuing.'),
('Review what is removed','Removal deletes your Members Portal membership and linked Emby/Ombi accounts and cancels linked subscriptions. It does not issue a refund. Historical financial records are retained with the member profile anonymised.'),
('Confirm only if you intend to delete','Enter your current password, tick the permanent-deletion confirmation and select Permanently delete my account. Use Keep my account if you do not want to proceed.'),
('Wait for completion','You are signed out immediately. Unavailable services are retried, and completion email is sent after removal and cancellation finish. Your separate global Emby Connect account is not deleted.')], 'Permanent deletion cannot be undone from the portal.')
]

ADMIN=[
guide('invitations','Create and manage invitations','Members & access','Invite a member with the right plan, libraries and administrator attribution.','/admin/invitations','invitations',[
('Open Invitations','Choose Invitations in the sidebar. If no servers exist, add one under Configuration before making an invitation.'),
('Set recipient and attribution','Enter an Invitation name and choose the Attributed administrator. That administrator receives revenue attribution; the sender is logged separately. Add a recipient email, or leave it blank for a shareable link.'),
('Choose expiry and payment','Set Expires in days and Places. Choose a Monthly plan, or No monthly plan — free access. The selected plan is fixed to the invitation.'),
('Choose media access','Select each intended server and its media libraries. Enable Allow Live TV only if wanted. Do not select unrelated libraries.'),
('Create and deliver','Select Create & send invitation. Review the result and email status. Share a generated link privately when you did not specify email.'),
('Manage sent invitations','Use Resend email if needed, Revoke to stop future joins, or Delete invitation to review and confirm its removal. Revocation, expiry and deletion do not remove already-joined members or erase revenue attribution.')]),
guide('member-permissions','Manage member access and libraries','Members & access','Change assigned-server permissions without overriding subscription eligibility.','/admin/members','members',[
('Open the member','Choose Members and open the relevant account. Check its email, verification, MFA, billing status and paid-through date.'),
('Review server state','Check setup, access and any sync error in the server table. An interrupted account operation needs operator investigation, not repeated changes.'),
('Select server permissions','Under Server & media permissions, choose Enable access to this server, the intended Media libraries, and Allow Live TV if required.'),
('Save and verify','Select Save [server name] permissions. Pending updates are displayed and retried; refresh to confirm they were applied.'),
('Suspend or restore membership','Suspend portal and server access blocks the managed membership. Restore membership eligibility still respects payment and verification. Suspension does not cancel billing. Imported accounts must be managed directly in Emby.')]),
guide('monthly-plans','Create and publish monthly plans','Billing','Save a plan, publish it to a payment provider and make it available on invitations.','/admin/plans','plans',[
('Open Monthly plans','Use the Monthly plans sidebar item. Check for a background-worker warning before trying to publish.'),
('Create the plan','Under Create a monthly plan, enter Plan name and Monthly price (GBP). Select optional provider publication, then Save monthly plan.'),
('Publish to a provider','For a saved plan, select Publish to Stripe or Publish to PayPal after configuring that provider. Saving locally alone does not make payment available.'),
('Check publication','Wait for Published / ready status. If a publication error appears, correct the provider configuration and use Retry. Avoid repeatedly creating the same plan.'),
('Use or revise the plan','Select the published plan when creating an invitation. Edit name or monthly price to change it; a price change creates a new version and preserves existing invitation/member prices.'),
('Retire a plan for new joins','Select Stop new invitations on this plan to remove it from new selections. This does not cancel existing subscribers.')]),
guide('emby-servers','Connect and refresh Emby servers','Configuration','Add a server and keep its library catalogue current.','/admin/settings#servers','servers',[
('Open Configuration','Find Connected Emby servers at the top of the page.'),
('Add a server','Expand Add a server +. Enter a recognizable Name, the Emby URL and its API key. Select Test & connect.'),
('Confirm libraries','Check that the saved server appears with its libraries. If the connection fails, check the URL, reachability and API key before retrying.'),
('Refresh an existing server','Expand its name and choose Check & refresh. Leave Replacement API key blank to preserve it, or supply a replacement when rotating keys.'),
('Assign access separately','Use Invitations for new members and Members → member details for existing managed accounts. Adding a server alone does not grant every member every library.')], 'Do not paste API keys into support messages or guide screenshots.'),
guide('email-and-ombi','Configure email and Ombi requests','Configuration','Set up account emails and automatic member request accounts.','/admin/settings','configuration',[
('Open Configuration','Find Email / SMTP and Ombi requests. Secret fields are write-only; leave existing secret fields blank to preserve them.'),
('Save SMTP details','Enter the SMTP host, port, encryption mode, username, password and From email supplied for your mail service. Choose Save email configuration.'),
('Test delivery','Choose Send test to my admin email. Check that it arrives, including junk mail. A saved configuration alone does not prove delivery.'),
('Configure Ombi','Enter Ombi base URL and its account-management API key, then Save Ombi configuration. The key must be able to manage regular users.'),
('Check member setup','Verify an invited member’s setup status. Eligible accounts are created/linked automatically. Searches, requests and history use each member’s own Ombi session; the management key is not their browsing identity.')]),
guide('payment-providers','Configure payment providers','Billing','Connect the supported payment provider before publishing paid plans.','/admin/settings','configuration',[
('Choose the correct environment','Use Configuration for Stripe or PayPal. Keep test and live credentials separate. Do not switch an installation with existing provider records into another merchant or environment.'),
('Configure Stripe','Enter the Secret or restricted API key and Webhook signing secret. Configure the webhook URL and event list displayed on the page in Stripe, then save. The worker must be running before billing is enabled.'),
('Configure PayPal if used','Select the Environment and enter Client ID, Client secret and Webhook ID. Configure the displayed webhook URL/events. Enabling checkout validates the webhook against PayPal.'),
('Publish monthly plans','Open Monthly plans and publish each intended plan to the configured provider. A plan with only a local price cannot yet be paid for.'),
('Verify the whole flow','Confirm the checkout, payment event and membership status using an appropriate test environment before offering live paid invitations. Approval alone is not payment, and test transactions do not unlock live access.')]),
guide('import-existing-users','Import and associate existing Emby users','Members & access','Link existing people to portal logins without assuming that matching names prove ownership.','/admin/imports','imports',[
('Refresh the inventory','Open Imported users and select Import / refresh all servers. The import reads Emby accounts without changing their permissions or passwords.'),
('Review each identity','Check server, local username, current Connect identity and status. Matching local names are suggestions, not proof that two accounts belong to the same person.'),
('Associate an existing member','Expand Associate with a portal member. Select the verified person and confirm with your current administrator password.'),
('Or create a portal login','Expand Create a portal login for this person. Supply the member email and portal username, confirm your administrator password, and optionally send the setup link by SMTP.'),
('Explain ongoing management','Imported Emby permissions and billing remain managed outside portal subscription enforcement. Association does not grant administrator rights. Later member password changes may sync to eligible associated accounts.'),
('Replace an unused setup link if needed','For an unverified associated member, use Replace member setup link. The new setup link replaces the old one.')]),
guide('administrator-access','Create or promote an administrator','Security & team','Give trusted staff portal access with mandatory MFA.','/admin/administrators','administrators',[
('Open Administrators','Review Current administrators and their setup or MFA status. An Emby server administrator is not automatically a portal administrator.'),
('Create a new administrator','Enter the new administrator’s email and username under Create an administrator. Confirm your current password and fresh authenticator or recovery code, and choose whether to email the setup link.'),
('Or promote a verified member','Choose the verified member under Promote an existing member, complete the same reauthentication and select Grant administrator role.'),
('Complete password setup and MFA','The new administrator chooses their own password from the single-use link, then enrols MFA. They cannot enter administration without MFA.'),
('Replace pending links carefully','Use Issue a replacement setup link only for an administrator whose setup is incomplete. Existing pending links are invalidated. Promotions also sign out existing sessions.')]),
guide('revenue-and-activity','Read revenue and recent activity','Reporting','Review collected payments and account events without confusing revenue with payouts.','/admin/revenue','revenue',[
('Open Admin revenue','Choose a Month (UTC) and Payment environment, then select Show monthly report.'),
('Read the totals','Collected payments minus refunds/reversals form Net receipts. Figures are before fees and taxes; they are not commissions or payouts. Test data is shown separately.'),
('Check attribution and detail','Payments are credited to the administrator selected on the original invitation, including renewals. Review Payment detail for provider, date and amount. Missing legacy attribution remains Unattributed.'),
('Review Recent activity','Open Recent activity in the sidebar to review recorded account actions. Use it alongside the affected member’s setup and billing state; it is not a live download monitor.'),
('Review pending removals','From Members, open Account removal queue for deletion progress. Removal retries may need a provider or operator issue resolved; retained financial history is not proof that a member account still exists.')])
]

# Official client download pages, reviewed 18 October 2026. No server installers.
WATCH_GUIDE=guide('watch-on-your-devices','Watch Emby on your phone, TV or computer','Watching Emby',
 'Find official downloads for Apple, Google Android, smart TVs, Windows and macOS, then sign in with Emby Connect.',
 'https://app.emby.media/','watch-emby',[
 ('Start watching in your browser','Choose Open Emby on your dashboard. It opens app.emby.media in a new tab. When Members Portal creates your Emby Connect account automatically, it uses the same email and password you chose when joining this site. On your first visit, use those details to sign in and choose your server. Existing Connect accounts keep their own sign-in details; later password changes are managed separately. A remembered Emby sign-in may take you straight back in; signing in to Members Portal alone does not sign you in to Emby.'),
 ('iPhone and iPad','Open the Apple App Store on your iPhone or iPad and search for Emby. Use the official iOS page below for the current download and requirements.'),
 ('Android phones and tablets','Open Google Play and search for Emby. The official Android page also covers the Amazon Appstore and manual downloads for compatible devices.'),
 ('Apple TV','On a supported Apple TV box, open the tvOS App Store and search for Emby. The Apple TV app built into another brand of television is not the same as an Apple TV streaming box.'),
 ('Google TV and Android TV: check the operating system','On TVs and boxes running Google TV or Android TV, open Google Play and search for Emby. This applies to compatible models from brands such as Sony, Philips, TCL, Hisense, Panasonic, Sharp, Toshiba and JVC, plus supported Google TV devices and NVIDIA Shield. Check your exact model: the brand name alone does not mean it runs Android or supports Emby.'),
 ('Samsung smart TVs','Search for Emby in the Samsung TV app store. If it is missing, check Emby’s official Samsung page for supported installation options, including its USB instructions. Availability depends on your TV model and region.'),
 ('LG smart TVs','Search for Emby in the LG TV app store on a compatible TV. Check the official LG page and your model’s store listing; not every older LG television has the same app support.'),
 ('Roku TVs and streaming players','Search for Emby in the Roku App Store. Use this route for a television actually running Roku TV, regardless of the manufacturer printed on it, or for a separate Roku player.'),
 ('Fire TV televisions and Fire TV Stick','Search for Emby in the Amazon Appstore on your Fire TV device. For televisions from different brands, check that the model actually uses Fire TV before following these instructions.'),
 ('Windows PCs','Use the official Emby for Windows download page for Windows 10 or 11. Install the viewing app, not Emby Server. Emby currently lists Premiere as required for playback in this desktop app; check with your administrator if you see an unlock prompt. You can also use Open Emby in your browser.'),
 ('Mac computers','Use the official macOS page for its Mac App Store link or current manual download. Install the viewing app, not Emby Server. Emby currently lists Premiere as required for playback in this desktop app. The browser link is also available on a Mac.'),
 ('Other TVs or no Emby app in your store','Check the operating system in your TV settings first. For VIDAA, Vizio, older or other TV systems, do not assume an app exists because another model from the same brand has it. Check Emby’s official app catalogue and your device’s store. If no compatible app is listed, use a supported external streaming player such as Apple TV, Roku, Fire TV or Google TV connected by HDMI. App availability and features vary by model and region.'),
 ('Connect the app to your account','Choose Emby Connect when offered and follow the sign-in screen. If your TV shows a PIN, open the address shown on that TV on your phone or computer and enter the code. For an automatically created Emby Connect account, sign in with the same email and password you chose when joining this site. If you already had an Emby Connect account or changed its password later, use its current sign-in details. Changing your portal password does not update Emby Connect. If no server appears, check your linked accounts or ask your administrator to check your Connect association. Do not create a second account just to retry.')],
 'Install an Emby viewing app, not Emby Server. You need an enabled server account and any required membership payment. Some apps or features require an unlock or Emby Premiere; check the app listing and your administrator before paying.')
WATCH_GUIDE.update(updated='18 October 2026',external=True)
for step,links in zip(WATCH_GUIDE['steps'],[
 [('Open Emby web app','https://app.emby.media/')],
 [('Official iPhone and iPad download','https://emby.media/emby-for-ios.html')],
 [('Official Android downloads','https://emby.media/emby-for-android.html')],
 [('Official Apple TV installation','https://emby.media/emby-for-appletv.html')],
 [('Official Android TV installation','https://emby.media/emby-for-android-tv.html')],
 [('Official Samsung installation and USB instructions','https://emby.media/emby-for-samsung-smart-tv.html')],
 [('Official LG installation','https://emby.media/emby-for-lg-smart-tv.html')],
 [('Official Roku installation','https://emby.media/emby-for-roku.html')],
 [('Official Fire TV installation','https://emby.media/emby-for-fire-tv.html')],
 [('Official Windows download','https://emby.media/emby-for-windows.html')],
 [('Official macOS downloads','https://emby.media/emby-theater-macos.html')],
 [('Official Emby apps and devices catalogue','https://emby.media/download.html')],
 [('Emby Connect help','https://emby.media/support/articles/Emby-Connect-for-Users.html')]
]):
 step['links']=[dict(label=label,url=url) for label,url in links]
MEMBER.insert(0,WATCH_GUIDE)
# The same public-device instructions are discoverable in both guide collections.
ADMIN.insert(0,WATCH_GUIDE)
