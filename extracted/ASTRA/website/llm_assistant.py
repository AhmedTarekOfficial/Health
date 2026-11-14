import os

from provider_config import get_llm_provider_status, invoke_groq_chat


class OrbitNexusLLMAssistant:
    """OrbitNexus AI Medical Assistant powered by Llama via Groq API."""
    
    SYSTEM_PROMPT = """You are OrbitNexus AI, the Autonomous Space Therapeutics & Health Intelligence System aboard the International Space Station. You monitor astronaut health telemetry in real-time and provide expert medical analysis.

Your capabilities:
- Analyze vital signs (heart rate, SpO2, respiration, blood pressure, core temperature)
- Assess stress levels and cognitive load from physiological markers  
- Detect anomalies in astronaut health patterns
- Evaluate spaceflight biological adaptation from genomic biomarkers
- Provide EVA/IVA readiness assessments
- Recommend countermeasures for space-related health issues (bone density loss, radiation exposure, circadian disruption, cardiovascular deconditioning)

Communication style:
- Use precise medical and aerospace terminology
- Be concise but thorough
- Prioritize crew safety in all recommendations
- Reference NASA medical standards when applicable
- Format responses with clear sections when analyzing data
- Use metric units (°C, BPM, mmHg)

Current mission context: ISS Expedition monitoring, crew of 6, orbital altitude 408 km."""

    def __init__(self, api_key=None, model=None):
        self.api_key = api_key or os.environ.get("GROQ_API_KEY")
        self.model = model or os.environ.get("GROQ_CHAT_MODEL") or "llama-3.3-70b-versatile"
        self.client = None
        self.conversation_history = []
        self.max_history = 10
        self.current_telemetry_context = None

        if self.api_key:
            try:
                from groq import Groq
                self.client = Groq(api_key=self.api_key)
            except ImportError:
                self.client = None
    
    @property
    def is_available(self):
        return self.client is not None
    
    def update_telemetry_context(self, telemetry_results):
        """Feed latest telemetry predictions to provide context for LLM."""
        self.current_telemetry_context = telemetry_results
    
    def chat(self, user_message):
        """Send a message and get a response from the LLM."""
        if not self.is_available:
            return {
                "response": "⚠️ OrbitNexus AI is offline. Set the GROQ_API_KEY environment variable to enable AI-powered health analysis.\n\nGet your free API key at: https://console.groq.com",
                "model": None,
                "status": "offline"
            }
        
        # Build context message with latest telemetry
        context = ""
        if self.current_telemetry_context:
            import json
            context = f"\n\n[CURRENT TELEMETRY DATA]\n{json.dumps(self.current_telemetry_context, indent=2)}\n"
        
        # Add user message to history
        self.conversation_history.append({"role": "user", "content": user_message})
        
        # Trim history
        if len(self.conversation_history) > self.max_history * 2:
            self.conversation_history = self.conversation_history[-self.max_history * 2:]
        
        # Build messages
        messages = [
            {"role": "system", "content": self.SYSTEM_PROMPT + context}
        ] + self.conversation_history
        
        try:
            provider_status = get_llm_provider_status()
            if provider_status["provider"] != "groq" or not provider_status["api_key_present"]:
                return {
                    "response": "⚠️ OrbitNexus AI is offline. Set LLM_PROVIDER=groq and GROQ_API_KEY to enable AI-powered health analysis.",
                    "model": self.model,
                    "status": "offline"
                }

            result = invoke_groq_chat(messages, model=self.model, temperature=0.7, max_tokens=1024)
            if result.get("status") == "ok":
                response_text = result["reply"]
                self.conversation_history.append({"role": "assistant", "content": response_text})
                return {
                    "response": response_text,
                    "model": self.model,
                    "status": "online",
                    "tokens_used": None,
                }
            return {
                "response": result.get("reply", "⚠️ OrbitNexus AI error."),
                "model": self.model,
                "status": result.get("status", "error"),
                "error_type": result.get("error_type")
            }
        except Exception as e:
            return {
                "response": f"⚠️ OrbitNexus AI error: {str(e)}",
                "model": self.model,
                "status": "error"
            }
    
    def get_health_summary(self):
        """Generate an automatic health summary from current telemetry."""
        return self.chat("Provide a comprehensive health status summary for CDR. NEXUS-1 based on the current telemetry data. Include risk assessment and recommendations.")
    
    def get_risk_analysis(self):
        return self.chat("Perform a detailed risk analysis based on current vital signs and activity patterns. Identify any concerning trends or anomalies.")
    
    def get_recommendations(self):
        return self.chat("Based on the astronaut's current health state and activity, provide specific countermeasure recommendations for optimal crew health maintenance.")
    
    def clear_history(self):
        self.conversation_history = []


# Backward compatibility alias
AstraLLMAssistant = OrbitNexusLLMAssistant
