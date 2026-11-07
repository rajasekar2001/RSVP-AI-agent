# Male / Female voice
Choose Default / Female / Male in the dashboard (Build -> Voice). Each outgoing call is sent with
`app_config.app_overrides.text_to_speech_config.speaker_name` (default female `ishita`, male `shubh`;
type any other Sarvam speaker name, lowercase) and, when speed is not 1.0,
`speech_settings.pace` (Sarvam clamps to 0.85-2.0). No extra Sarvam agents or .env values are needed.

The same override exists in Sarvam's SDK (`TextToSpeechConfig`), but the phone-call REST page does not
show it, so it is not confirmed for phone calls. If Sarvam refuses it, the call is still placed with the
agent's own voice and the contact row shows "Sarvam did not accept a per-call voice". In that case use
the optional fallback: two Sarvam agents with different speakers and set SARVAM_APP_ID_FEMALE /
SARVAM_APP_ID_MALE (see SARVAM_AGENT_SETUP.md). Those take priority when set.
Incoming call-backs use the voice set on the inbound agent in Sarvam.
