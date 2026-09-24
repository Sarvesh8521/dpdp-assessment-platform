import React, { useState, useEffect } from 'react';
import { useGoogleLogin } from '@react-oauth/google';
import './index.css';

const API_BASE = import.meta.env.VITE_API_URL || 'http://localhost:8001/api/v1';

const StatCard = ({ title, value, subtitle, type = 'info' }: any) => {
  const badgeClass = `badge badge-${type}`;
  return (
    <div className="card glass-panel flex-col gap-2">
      <h3 style={{ fontSize: '0.875rem', color: 'var(--text-secondary)' }}>{title}</h3>
      <div className="flex items-center gap-4">
        <span style={{ fontSize: '2rem', fontWeight: 'bold', color: 'var(--text-primary)' }}>{value}</span>
        {subtitle && <span className={badgeClass}>{subtitle}</span>}
      </div>
    </div>
  );
};

/* ─── Google SVG Icon ─── */
const GoogleIcon = () => (
  <svg viewBox="0 0 24 24" width="20" height="20" xmlns="http://www.w3.org/2000/svg">
    <path d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z" fill="#4285F4"/>
    <path d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z" fill="#34A853"/>
    <path d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z" fill="#FBBC05"/>
    <path d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z" fill="#EA4335"/>
  </svg>
);

/* ─── Integration State Type ─── */
type IntegrationState = {
  status: 'not_connected' | 'connecting' | 'connected' | 'error';
  connectionId?: string;
  accountEmail?: string;
  accountName?: string;
  resourceId?: string;
  resourceName?: string;
  resourceSelected?: boolean;
  requiresResourceSelection?: boolean;
  tempTokenRef?: string;
  resources?: { id: string; name: string; metadata?: any }[];
  providerMetadata?: any;
  error?: string;
};

function App() {
  /* ─── Navigation & Session ─── */
  const [activeTab, setActiveTab] = useState(() => {
    const path = window.location.pathname;
    if (path.includes('new_assessment') || path.includes('integrations')) {
      return 'new_assessment';
    }
    if (path.includes('status')) return 'status';
    if (path.includes('assessments')) return 'assessments';
    return 'dashboard';
  });
  const [token, setToken] = useState<string | null>(() => localStorage.getItem('dpdp_token'));
  const [clientId, setClientId] = useState<string | null>(() => localStorage.getItem('dpdp_client_id'));

  /* ─── Login & Auth State ─── */
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [loginError, setLoginError] = useState('');
  const [isLoggingIn, setIsLoggingIn] = useState(false);
  const [authMode, setAuthMode] = useState<'signin' | 'signup' | 'forgot'>('signin');
  const [authMethod, setAuthMethod] = useState<'password' | 'otp'>('password');
  const [otpEmail, setOtpEmail] = useState('');
  const [otpCode, setOtpCode] = useState('');
  const [otpSent, setOtpSent] = useState(false);
  const [otpCountdown, setOtpCountdown] = useState(0);
  const [forgotStep, setForgotStep] = useState<1 | 2>(1);
  const [resetOtp, setResetOtp] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [resetMsg, setResetMsg] = useState('');

  /* ─── App Assessment State ─── */
  const [assessments, setAssessments] = useState<any[]>([]);
  const [loading, setLoading] = useState(false);
  const [errorMsg, setErrorMsg] = useState('');
  const [findings, setFindings] = useState<any[]>([]);
  
  /* ─── Human Review Override State ─── */
  const [overrideModalOpen, setOverrideModalOpen] = useState(false);
  const [overrideFinding, setOverrideFinding] = useState<any>(null);
  const [overrideVerdict, setOverrideVerdict] = useState<string>('Observed');
  const [overrideReason, setOverrideReason] = useState<string>('');

  /* ─── Integration Connections State ─── */
  const [connections, setConnections] = useState<Record<string, any>>({});
  const [clientName, setClientName] = useState<string>('My Organization');

  /* ─── Manual Form Inputs for Integrations ─── */
  const [adsCustomerId, setAdsCustomerId] = useState('');
  const [cmpKey, setCmpKey] = useState('');
  const [cmpProvider, setCmpProvider] = useState('OneTrust');
  const [selectedBiz, setSelectedBiz] = useState('');
  const [selectedAdAccount, setSelectedAdAccount] = useState('');
  const [metaAssets, setMetaAssets] = useState<any[]>([]);
  const [selectedRepo, setSelectedRepo] = useState('');
  const [cmpSite, setCmpSite] = useState('');
  const [selectedResource, setSelectedResource] = useState<Record<string, string>>({});
  const [manualInputs, setManualInputs] = useState<Record<string, string>>({});
  const [editingProvider, setEditingProvider] = useState<string | null>(null);

  /* ─── Active Assessment View ─── */
  const [activeAssessmentId, setActiveAssessmentId] = useState<string | null>(null);
  const [activeAssessment, setActiveAssessment] = useState<any>(null);
  const [reportData, setReportData] = useState<any>(null);
  const [reportIntegrationTab, setReportIntegrationTab] = useState<string>('combined');

  /* ────────────────────────────────────────────────────────
   * ALL HOOKS MUST BE DECLARED HERE AT THE TOP LEVEL
   * ──────────────────────────────────────────────────────── */

  /* ─── Hook 1: Google Login (Sign-In Button on Login Page) ─── */
  const googleLogin = useGoogleLogin({
    flow: 'implicit',
    onSuccess: async (tokenResponse) => {
      setIsLoggingIn(true);
      setLoginError('');
      try {
        const res = await fetch(`${API_BASE}/auth/google/token`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ access_token: tokenResponse.access_token }),
        });
        if (!res.ok) {
          const err = await res.json().catch(() => null);
          setLoginError(err?.detail || 'Google authentication failed');
          return;
        }
        const data = await res.json();
        await completeLogin(data.access_token);
      } catch (err: any) {
        setLoginError(`Connection error: ${err.message}`);
      } finally {
        setIsLoggingIn(false);
      }
    },
    onError: (error) => {
      setLoginError(`Google login failed: ${error.error_description || error.error || 'Unknown error'}`);
    },
  });

  /* ─── OTP Countdown Timer Hook ─── */
  useEffect(() => {
    if (otpCountdown <= 0) return;
    const timer = setInterval(() => {
      setOtpCountdown((prev) => (prev > 0 ? prev - 1 : 0));
    }, 1000);
    return () => clearInterval(timer);
  }, [otpCountdown]);

  /* ─── Hook 2: OAuth Return Handler from Backend Callback ─── */
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const provider = params.get('provider');
    const status = params.get('status');
    const error = params.get('error');
    const connectionId = params.get('connection_id');
    const accountEmail = params.get('account_email');

    if (provider && (status || error)) {
      setActiveTab('new_assessment');
      if (error) {
        setIntState(provider, { status: 'error', error });
      } else if (status === 'connected') {
        setIntState(provider, {
          status: 'connected',
          connectionId: connectionId || undefined,
          accountEmail: accountEmail || undefined,
        });
        if (token && clientId) {
          fetchResourcesForProvider(provider, token, clientId);
          loadIntegrationStatuses(token, clientId);
        }
      }
      // Remove OAuth callback query params from URL without refreshing
      window.history.replaceState({}, document.title, '/new_assessment');
    }
  }, [token, clientId]);

  /* ─── Hook 3: Load Integrations on Tab Change or Auth ─── */
  useEffect(() => {
    if (token && clientId && (activeTab === 'new_assessment' || activeTab === 'integrations')) {
      loadIntegrationStatuses(token, clientId);
    }
  }, [token, clientId, activeTab]);

  /* ─── Hook 4: Restore Session from localStorage ─── */
  useEffect(() => {
    const savedToken = localStorage.getItem('dpdp_token');
    if (savedToken) {
      completeLogin(savedToken);
    }
  }, []);

  /* ─── Hook 5: Polling Assessment Status ─── */
  useEffect(() => {
    let interval: any;
    if (activeTab === 'status' && activeAssessmentId && token) {
      pollAssessment(activeAssessmentId);
      interval = setInterval(() => {
        if (activeAssessment && (activeAssessment.status === 'pending' || activeAssessment.status === 'running')) {
          pollAssessment(activeAssessmentId);
        }
      }, 3000);
    }
    return () => clearInterval(interval);
  }, [activeTab, activeAssessmentId, activeAssessment?.status, token]);

  /* ─── Hook 6: Refresh Assessments on History Tab ─── */
  useEffect(() => {
    if (activeTab === 'assessments' && token) {
      fetchAssessments(token);
    }
  }, [activeTab, token]);

  /* ─── Hook 7: Fetch Meta Ad Accounts ─── */
  useEffect(() => {
    if (selectedBiz && token && clientId) {
      fetchMetaAssets(selectedBiz, token, clientId);
    } else {
      setMetaAssets([]);
      setSelectedAdAccount('');
    }
  }, [selectedBiz, token, clientId]);

  const loadIntegrationStatuses = async (authToken: string, cid: string) => {
    try {
      const res = await fetch(`${API_BASE}/integrations/status?client_id=${cid}`, {
        headers: { 'Authorization': `Bearer ${authToken}` },
      });
      if (res.ok) {
        const data = await res.json();
        const newConns: Record<string, any> = {};
        for (const item of data.integrations || []) {
          newConns[item.provider] = {
            status: item.status,
            connectionId: item.connection_id,
            accountEmail: item.account_email,
            accountName: item.account_name,
            resourceId: item.resource_id,
            resourceName: item.resource_name,
            resourceSelected: item.resource_selected ?? false,
            requiresResourceSelection: item.requires_resource_selection ?? true,
            providerMetadata: item.provider_metadata,
            error: item.error,
          };
          
          if (item.provider === 'meta' && item.provider_metadata?.business_id) {
            setSelectedBiz(item.provider_metadata.business_id);
          }

          // If connected but no resource selected, fetch resources to display in selector
          if (item.status === 'connected' && !item.resource_selected) {
            fetchResourcesForProvider(item.provider, authToken, cid);
          }
        }
        setConnections((prev) => ({ ...prev, ...newConns }));
      }
    } catch (e) {
      console.warn('Failed to load integration status', e);
    }
  };

  const fetchResourcesForProvider = async (provider: string, authToken: string, cid: string) => {
    if (['gtm', 'ga4', 'search_console', 'meta', 'github', 'google_ads'].includes(provider)) {
      try {
        const listRes = await fetch(
          `${API_BASE}/integrations/${provider}/resources?client_id=${cid}`,
          { headers: { 'Authorization': `Bearer ${authToken}` } }
        );
        if (listRes.ok) {
          const listData = await listRes.json();
          setIntState(provider, {
            resources: listData.resources || [],
          });
        } else {
          // Fetch failed — set empty array so UI shows "no repos" + URL input instead of loading forever
          console.warn(`Resource fetch returned ${listRes.status} for ${provider}`);
          setIntState(provider, { resources: [] });
        }
      } catch (e) {
        console.warn(`Failed to fetch resources for ${provider}`, e);
        setIntState(provider, { resources: [] });
      }
    }
  };

  const fetchMetaAssets = async (businessId: string, authToken: string, cid: string) => {
    try {
      const res = await fetch(`${API_BASE}/integrations/meta/resources/${businessId}/assets?client_id=${cid}`, {
        headers: { 'Authorization': `Bearer ${authToken}` },
      });
      if (res.ok) {
        const data = await res.json();
        setMetaAssets(data.resources || []);
      } else {
        setMetaAssets([]);
      }
    } catch (e) {
      console.warn('Failed to fetch meta assets', e);
      setMetaAssets([]);
    }
  };

  const completeLogin = async (accessToken: string) => {
    setToken(accessToken);
    localStorage.setItem('dpdp_token', accessToken);
    try {
      const meRes = await fetch(`${API_BASE}/auth/me`, {
        headers: { 'Authorization': `Bearer ${accessToken}` },
      });
      if (meRes.ok) {
        const meData = await meRes.json();
        const cid = Object.keys(meData.client_roles || {})[0];
        if (cid) {
          setClientId(cid);
          localStorage.setItem('dpdp_client_id', cid);
          loadIntegrationStatuses(accessToken, cid);
          try {
            const clientRes = await fetch(`${API_BASE}/clients/${cid}`, {
              headers: { 'Authorization': `Bearer ${accessToken}` },
            });
            if (clientRes.ok) {
              const clientData = await clientRes.json();
              if (clientData.name) {
                setClientName(clientData.name);
              }
            } else if (meData.display_label || meData.identity) {
              setClientName(meData.display_label || meData.identity);
            }
          } catch {
            if (meData.display_label || meData.identity) {
              setClientName(meData.display_label || meData.identity);
            }
          }
        }
        fetchAssessments(accessToken);
      }
    } catch (err) {
      // Ignored
    }
  };

  const handleLogout = () => {
    setToken(null);
    setClientId(null);
    localStorage.removeItem('dpdp_token');
    localStorage.removeItem('dpdp_client_id');
    setAssessments([]);
    setFindings([]);
    setActiveTab('dashboard');
    setEmail('');
    setPassword('');
    setOtpEmail('');
    setOtpCode('');
    setOtpSent(false);
    setOtpCountdown(0);
    setForgotStep(1);
    setResetOtp('');
    setNewPassword('');
    setConfirmPassword('');
    setLoginError('');
    setResetMsg('');
  };

  const handlePasswordSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    const cleanEmail = email.trim();
    if (!cleanEmail || !password) {
      setLoginError('Please enter both email and password.');
      return;
    }
    if (authMode === 'signup' && password.length < 6) {
      setLoginError('Password must be at least 6 characters long.');
      return;
    }

    setIsLoggingIn(true);
    setLoginError('');
    try {
      const endpoint = authMode === 'signin' ? '/auth/login' : '/auth/register';
      const res = await fetch(`${API_BASE}${endpoint}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email: cleanEmail, password }),
      });
      if (!res.ok) {
        const errorData = await res.json().catch(() => null);
        setLoginError(errorData?.detail || `${authMode === 'signin' ? 'Login' : 'Registration'} failed`);
        return;
      }
      const data = await res.json();
      await completeLogin(data.access_token);
    } catch (err: any) {
      setLoginError(`Connection error: ${err.message}`);
    } finally {
      setIsLoggingIn(false);
    }
  };

  const handleSendLoginOtp = async (e?: React.FormEvent) => {
    if (e) e.preventDefault();
    const targetEmail = (otpEmail.trim() || email.trim()).toLowerCase();
    if (!targetEmail || !targetEmail.includes('@')) {
      setLoginError('Please enter a valid email address.');
      return;
    }
    if (!otpEmail) setOtpEmail(targetEmail);

    setIsLoggingIn(true);
    setLoginError('');
    try {
      const res = await fetch(`${API_BASE}/auth/otp/send-login`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email: targetEmail }),
      });
      const data = await res.json().catch(() => null);
      if (!res.ok) {
        setLoginError(data?.detail || 'Failed to send login code.');
        return;
      }
      setOtpSent(true);
      setOtpCountdown(60);
      setResetMsg(data?.message || `Verification code sent to ${targetEmail}`);
    } catch (err: any) {
      setLoginError(`Connection error: ${err.message}`);
    } finally {
      setIsLoggingIn(false);
    }
  };

  const handleVerifyLoginOtp = async (e: React.FormEvent) => {
    e.preventDefault();
    const cleanOtp = otpCode.trim();
    if (!cleanOtp || cleanOtp.length !== 6) {
      setLoginError('Please enter the 6-digit verification code.');
      return;
    }

    setIsLoggingIn(true);
    setLoginError('');
    try {
      const res = await fetch(`${API_BASE}/auth/otp/verify-login`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email: otpEmail.trim().toLowerCase(), otp: cleanOtp }),
      });
      if (!res.ok) {
        const errorData = await res.json().catch(() => null);
        setLoginError(errorData?.detail || 'Verification failed. Please check the code.');
        return;
      }
      const data = await res.json();
      await completeLogin(data.access_token);
    } catch (err: any) {
      setLoginError(`Connection error: ${err.message}`);
    } finally {
      setIsLoggingIn(false);
    }
  };

  const handleForgotSendOtp = async (e?: React.FormEvent) => {
    if (e) e.preventDefault();
    const targetEmail = email.trim().toLowerCase();
    if (!targetEmail || !targetEmail.includes('@')) {
      setLoginError('Please enter a valid email address.');
      return;
    }

    setIsLoggingIn(true);
    setLoginError('');
    setResetMsg('');
    try {
      const res = await fetch(`${API_BASE}/auth/forgot-password`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email: targetEmail }),
      });
      const data = await res.json().catch(() => null);
      if (!res.ok) {
        setLoginError(data?.detail || 'Failed to send reset code.');
        return;
      }
      setForgotStep(2);
      setOtpCountdown(60);
      setResetMsg(data?.message || 'A 6-digit password reset code has been sent to your email.');
    } catch (err: any) {
      setLoginError(`Error: ${err.message}`);
    } finally {
      setIsLoggingIn(false);
    }
  };

  const handleResetPasswordOtp = async (e: React.FormEvent) => {
    e.preventDefault();
    const cleanOtp = resetOtp.trim();
    if (!cleanOtp || cleanOtp.length !== 6) {
      setLoginError('Please enter the 6-digit reset code.');
      return;
    }
    if (!newPassword || newPassword.length < 6) {
      setLoginError('New password must be at least 6 characters long.');
      return;
    }
    if (newPassword !== confirmPassword) {
      setLoginError('Passwords do not match. Please re-enter.');
      return;
    }

    setIsLoggingIn(true);
    setLoginError('');
    try {
      const res = await fetch(`${API_BASE}/auth/reset-password`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          email: email.trim().toLowerCase(),
          otp: cleanOtp,
          new_password: newPassword,
        }),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => null);
        setLoginError(err?.detail || 'Reset failed');
        return;
      }
      setResetMsg('Password reset successfully! You can now sign in with your new password.');
      setResetOtp('');
      setNewPassword('');
      setConfirmPassword('');
      setTimeout(() => {
        setAuthMode('signin');
        setAuthMethod('password');
        setForgotStep(1);
        setResetMsg('');
        setLoginError('');
      }, 2500);
    } catch (err: any) {
      setLoginError(`Error: ${err.message}`);
    } finally {
      setIsLoggingIn(false);
    }
  };

  const fetchAssessments = async (authToken: string) => {
    try {
      const res = await fetch(`${API_BASE}/assessments`, {
        headers: { 'Authorization': `Bearer ${authToken}` },
      });
      if (res.ok) {
        const data = await res.json();
        setAssessments(data.items || []);
        if (data.items && data.items.length > 0) {
          fetchFindings(authToken, data.items[0].id);
        }
      }
    } catch (err) {}
  };

  const fetchFindings = async (authToken: string, assessmentId: string) => {
    try {
      const res = await fetch(`${API_BASE}/assessments/${assessmentId}/findings`, {
        headers: { 'Authorization': `Bearer ${authToken}` },
      });
      if (res.ok) {
        const data = await res.json();
        setFindings(data.items || []);
      }
    } catch (err) {}
  };

  const submitOverride = async () => {
    if (!overrideFinding || !overrideReason.trim()) return;
    try {
      const res = await fetch(`${API_BASE}/findings/${overrideFinding.id}/override`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${token}`
        },
        body: JSON.stringify({
          new_verdict: overrideVerdict.toUpperCase().replace(' ', '_'),
          reason: overrideReason
        })
      });
      if (res.ok) {
        setOverrideModalOpen(false);
        setOverrideFinding(null);
        setOverrideReason('');
        if (assessments.length > 0) {
          fetchFindings(token!, assessments[0].id);
          fetchReportData(token!, assessments[0].id);
        }
      } else {
        const error = await res.json();
        alert(error.detail || 'Failed to submit override');
      }
    } catch (err) {
      alert('Error submitting override');
    }
  };

  const fetchReportData = async (authToken: string, assessmentId: string) => {
    try {
      const res = await fetch(`${API_BASE}/assessments/${assessmentId}/report/json`, {
        headers: { 'Authorization': `Bearer ${authToken}` },
      });
      if (res.ok) {
        const data = await res.json();
        setReportData(data);
      }
    } catch (err) {}
  };

  const pollAssessment = async (id: string) => {
    try {
      const res = await fetch(`${API_BASE}/assessments/${id}`, {
        headers: { 'Authorization': `Bearer ${token}` },
      });
      if (res.ok) {
        const data = await res.json();
        setActiveAssessment(data);
        if (data.status === 'completed' || data.status === 'partial') {
          fetchFindings(token!, id);
          fetchReportData(token!, id);
          fetchAssessments(token!);
        }
      }
    } catch (e) {}
  };

  const viewAssessmentReport = async (assessmentItem: any) => {
    setActiveAssessmentId(assessmentItem.id);
    setActiveAssessment(assessmentItem);
    setReportIntegrationTab('combined');
    if (token) {
      await fetchFindings(token, assessmentItem.id);
      await fetchReportData(token, assessmentItem.id);
    }
    setActiveTab('status');
  };

  const handleStartAssessment = async () => {
    if (!token || !clientId) return;
    setLoading(true);
    setErrorMsg('');

    const activeIntegrations = Object.keys(connections).filter(
      (k) => (connections[k] as any)?.status === 'connected'
    );
    const effectiveAdsId = adsCustomerId || connections['google_ads']?.resourceId || connections['google_ads']?.resourceName || '';

    try {
      const res = await fetch(`${API_BASE}/assessments`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${token}`,
        },
        body: JSON.stringify({
          client_id: clientId,
          config: {
            integrations: activeIntegrations,
            ad_accounts: activeIntegrations.includes('google_ads') && effectiveAdsId ? [effectiveAdsId] : [],
            github_repos: activeIntegrations.includes('github') ? [
              connections['github']?.resourceId || connections['github']?.resourceName || selectedRepo
            ].filter(Boolean) : [],
            cmp_provider: activeIntegrations.includes('cmp') ? cmpProvider : null,
          },
        }),
      });
      if (res.ok) {
        const data = await res.json();
        setActiveAssessmentId(data.id);
        setActiveTab('status');
      } else {
        setErrorMsg('Failed to create assessment');
      }
    } catch (err: any) {
      setErrorMsg(`Error: ${err.message}`);
    } finally {
      setLoading(false);
    }
  };

  const handleExportPDF = (integrationKey?: string, targetId?: string) => {
    const id = targetId || activeAssessmentId;
    if (id && token) {
      const intParam = integrationKey && integrationKey !== 'combined' ? `&integration=${integrationKey}` : '';
      window.open(`${API_BASE}/assessments/${id}/report/pdf?token=${token}${intParam}`, '_blank');
    }
  };

  const handleExportJSON = async (integrationKey?: string, targetId?: string) => {
    const id = targetId || activeAssessmentId;
    if (!id || !token) return;
    try {
      const intParam = integrationKey && integrationKey !== 'combined' ? `?integration=${integrationKey}&token=${token}` : `?token=${token}`;
      const res = await fetch(`${API_BASE}/assessments/${id}/report/json${intParam}`, {
        headers: { 'Authorization': `Bearer ${token}` },
      });
      if (!res.ok) {
        setErrorMsg('Failed to download report JSON');
        return;
      }
      const blob = await res.blob();
      const url = window.URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      const keySuffix = integrationKey && integrationKey !== 'combined' ? `_${integrationKey}` : '_combined';
      a.download = `dpdp_report_${id}${keySuffix}.json`;
      document.body.appendChild(a);
      a.click();
      window.URL.revokeObjectURL(url);
      document.body.removeChild(a);
    } catch (err: any) {
      setErrorMsg(`Download error: ${err.message}`);
    }
  };

  /* ─── Integration State Helpers ─── */
  const getIntState = (provider: string): IntegrationState => {
    return (connections[provider] as IntegrationState) || { status: 'not_connected' };
  };

  const setIntState = (provider: string, update: Partial<IntegrationState>) => {
    setConnections((prev) => {
      const cur = (prev[provider] as IntegrationState) || { status: 'not_connected' };
      return {
        ...prev,
        [provider]: { ...cur, ...update },
      };
    });
  };

  /* ─── Real Google OAuth Flow (Full-page Redirect) ─── */
  const handleGoogleOAuth = async (provider: string) => {
    setIntState(provider, { status: 'connecting' });
    try {
      const res = await fetch(`${API_BASE}/integrations/oauth/google/init?provider=${provider}&client_id=${clientId}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${token}` },
      });
      if (!res.ok) {
        const err = await res.json().catch(() => null);
        throw new Error(err?.detail || 'Failed to initiate Google OAuth');
      }
      const data = await res.json();
      window.location.href = data.auth_url;
    } catch (e: any) {
      setIntState(provider, { status: 'error', error: e.message });
    }
  };

  /* ─── Real Meta OAuth Flow (Full-page Redirect) ─── */
  const handleMetaOAuth = async () => {
    setIntState('meta', { status: 'connecting' });
    try {
      const res = await fetch(`${API_BASE}/integrations/oauth/meta/init?client_id=${clientId}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${token}` },
      });
      if (!res.ok) {
        const err = await res.json().catch(() => null);
        throw new Error(err?.detail || 'Failed to initiate Meta OAuth');
      }
      const data = await res.json();
      window.location.href = data.auth_url;
    } catch (e: any) {
      setIntState('meta', { status: 'error', error: e.message });
    }
  };

  /* ─── Real GitHub App Flow (Full-page Redirect) ─── */
  const handleGitHubAuth = async () => {
    setIntState('github', { status: 'connecting' });
    try {
      const res = await fetch(`${API_BASE}/integrations/oauth/github/init?client_id=${clientId}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${token}` },
      });
      if (!res.ok) {
        const err = await res.json().catch(() => null);
        throw new Error(err?.detail || 'Failed to initiate GitHub authorization');
      }
      const data = await res.json();
      window.location.href = data.auth_url;
    } catch (e: any) {
      setIntState('github', { status: 'error', error: e.message });
    }
  };

  /* ─── Finalize Connection & Resource Selection ─── */
  const finalizeConnection = async (
    provider: string,
    resource: { id: string; name: string } | null,
    extra?: Record<string, any>
  ) => {
    const state = getIntState(provider);
    try {
      if (['gtm', 'ga4', 'search_console', 'meta', 'github', 'google_ads'].includes(provider) && resource) {
        const urlParams = new URLSearchParams({
          client_id: clientId || '',
          resource_id: resource.id,
          resource_name: resource.name,
        });
        if (state.connectionId) {
          urlParams.append('connection_id', state.connectionId);
        }
        const res = await fetch(
          `${API_BASE}/integrations/connect/${provider}/select-resource?${urlParams.toString()}`,
          {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${token}` },
            body: extra ? JSON.stringify({ provider_metadata: extra }) : undefined,
          }
        );
        if (res.ok) {
          const savedData = await res.json().catch(() => null);
          setIntState(provider, {
            status: 'connected',
            resourceId: savedData?.resource_id || resource.id,
            resourceName: savedData?.resource_name || resource.name,
            resourceSelected: true,
            connectionId: savedData?.connection_id || undefined,
          });
          // Refresh integration status from backend so persisted value survives refresh
          if (token && clientId) {
            loadIntegrationStatuses(token, clientId);
          }
          return;
        } else {
          const err = await res.json().catch(() => null);
          setIntState(provider, { status: 'error', error: err?.detail || 'Failed to save resource selection' });
          throw new Error(err?.detail || 'Failed to save resource selection');
        }
      }

      const res = await fetch(`${API_BASE}/integrations/connect?client_id=${clientId}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${token}` },
        body: JSON.stringify({
          provider,
          oauth_code: extra?.oauth_code,
          provider_name: extra?.provider_name,
          account_email: state.accountEmail,
          account_name: state.accountName,
          resource: resource ? { id: resource.id, name: resource.name, ...extra } : extra,
          ...extra,
        }),
      });
      if (res.ok) {
        const data = await res.json();
        setIntState(provider, {
          status: 'connected',
          connectionId: data.connection_id,
          accountEmail: state.accountEmail,
          accountName: state.accountName,
          resourceId: resource?.id || extra?.customer_id || extra?.site_id,
          resourceName: resource?.name || extra?.customer_id || extra?.site_name,
        });
      } else {
        const err = await res.json().catch(() => null);
        setIntState(provider, { status: 'error', error: err?.detail || 'Connection failed' });
      }
    } catch (e: any) {
      setIntState(provider, { status: 'error', error: e.message });
    }
  };

  /* ─── Disconnect ─── */
  const handleDisconnect = async (provider: string) => {
    const state = getIntState(provider);
    if (!state.connectionId) {
      setConnections((prev) => {
        const n = { ...prev };
        delete n[provider];
        return n;
      });
      return;
    }
    try {
      await fetch(`${API_BASE}/integrations/${state.connectionId}/disconnect`, {
        method: 'POST',
        headers: { 'Authorization': `Bearer ${token}` },
      });
    } catch {}
    setConnections((prev) => {
      const n = { ...prev };
      delete n[provider];
      return n;
    });
  };

  /* ────────────────────────────────────────────────────────
   * SUBCOMPONENTS FOR RENDERING
   * ──────────────────────────────────────────────────────── */

  const StatusBadge = ({ status }: { status: string }) => {
    const labels: Record<string, string> = {
      not_connected: 'Not Connected',
      connecting: 'Connecting',
      connected: 'Connected',
      error: 'Error',
    };
    return (
      <span className={`status-badge ${status.replace('_', '-')}`}>
        <span className="status-dot" />
        {labels[status] || status}
      </span>
    );
  };

  const ConnectedInfo = ({
    state,
    onReconnect,
    onDisconnect,
    onChangeResource,
    changeLabel = 'Change Resource',
  }: {
    state: IntegrationState;
    onReconnect: () => void;
    onDisconnect: () => void;
    onChangeResource?: () => void;
    changeLabel?: string;
  }) => (
    <div className="integration-card-body animate-fade-in">
      <div className="connected-info">
        {state.accountEmail && (
          <div className="connected-info-row">
            <span className="connected-info-label">Account:</span>
            <span className="connected-info-value">{state.accountEmail}</span>
          </div>
        )}
        {state.resourceName ? (
          <div className="connected-info-row">
            <span className="connected-info-label">Resource:</span>
            <span className="connected-info-value">{state.resourceName}</span>
          </div>
        ) : state.requiresResourceSelection !== false ? (
          <div className="connected-info-row">
            <span className="connected-info-label">Resource:</span>
            <span className="connected-info-value" style={{ color: 'var(--color-warning, #f59e0b)' }}>⚠ Not selected</span>
          </div>
        ) : null}
      </div>
      <div className="integration-actions">
        {onChangeResource && (
          <button
            className="btn-secondary"
            style={{ fontSize: '0.8rem', padding: '0.35rem 0.75rem' }}
            onClick={onChangeResource}
          >
            {changeLabel}
          </button>
        )}
        <button className="btn-reconnect" onClick={onReconnect}>
          Reconnect
        </button>
        <button className="btn-disconnect" onClick={onDisconnect}>
          Disconnect
        </button>
      </div>
    </div>
  );

  const GoogleIntegrationCard = ({
    provider,
    title,
    resourceLabel,
  }: {
    provider: string;
    title: string;
    resourceLabel: string;
  }) => {
    const state = getIntState(provider);
    const curSelected = selectedResource[provider] || '';
    const manualInput = manualInputs[provider] || '';
    const [isEditingResource, setIsEditingResource] = useState(false);

    const isConnected = state.status === 'connected';
    const isConnectingWithAccount = state.status === 'connecting' && Boolean(state.accountEmail);
    const hasSelectedResource = Boolean(state.resourceName || state.resourceId);

    return (
      <div className={`integration-card status-${state.status.replace('_', '-')}`}>
        <div className="integration-card-header">
          <div className="integration-card-title">
            <div className="integration-icon google">
              <GoogleIcon />
            </div>
            <h4>{title}</h4>
          </div>
          <StatusBadge status={state.status} />
        </div>

        {isConnected && hasSelectedResource && !isEditingResource ? (
          <ConnectedInfo
            state={state}
            onChangeResource={() => setIsEditingResource(true)}
            changeLabel={`Change ${resourceLabel}`}
            onReconnect={() => {
              handleDisconnect(provider);
              handleGoogleOAuth(provider);
            }}
            onDisconnect={() => handleDisconnect(provider)}
          />
        ) : (isConnected && (!hasSelectedResource || isEditingResource)) || isConnectingWithAccount ? (
          <div className="integration-card-body animate-fade-in">
            <div className="connected-info">
              <div className="connected-info-row">
                <span className="connected-info-label">Connected:</span>
                <span className="connected-info-value">{state.accountEmail}</span>
              </div>
            </div>

            {state.resources && state.resources.length > 0 ? (
              <div>
                <label className="integration-label">Select {resourceLabel}</label>
                <select
                  className="integration-select"
                  value={curSelected}
                  onChange={(e) => setSelectedResource((prev) => ({ ...prev, [provider]: e.target.value }))}
                >
                  <option value="">Select {resourceLabel}...</option>
                  {state.resources.map((r) => (
                    <option key={r.id} value={r.id}>
                      {r.name}
                    </option>
                  ))}
                </select>
              </div>
            ) : (
              <div>
                <label className="integration-label">Enter {resourceLabel} ID</label>
                <input
                  type="text"
                  className="integration-input"
                  placeholder={`e.g. ${provider === 'gtm' ? 'GTM-XXXXXX' : provider === 'ga4' ? '123456789' : 'https://example.com'}`}
                  value={manualInput}
                  onChange={(e) => setManualInputs((prev) => ({ ...prev, [provider]: e.target.value }))}
                />
              </div>
            )}

            <div style={{ display: 'flex', gap: '0.5rem', marginTop: '0.5rem' }}>
              <button
                className="btn-connect"
                disabled={!curSelected && !manualInput}
                onClick={async () => {
                  const found = (state.resources || []).find((r) => r.id === curSelected);
                  const finalResource = found || (manualInput ? { id: manualInput, name: manualInput } : null);
                  if (finalResource) {
                    await finalizeConnection(provider, finalResource);
                    setIsEditingResource(false);
                  }
                }}
              >
                Confirm & Connect
              </button>
              {isEditingResource && (
                <button
                  className="btn-secondary"
                  style={{ fontSize: '0.8rem', padding: '0.4rem 0.8rem' }}
                  onClick={() => setIsEditingResource(false)}
                >
                  Cancel
                </button>
              )}
            </div>

            <div className="integration-actions" style={{ marginTop: '0.75rem' }}>
              <button
                className="btn-reconnect"
                onClick={() => {
                  handleDisconnect(provider);
                  handleGoogleOAuth(provider);
                }}
              >
                Reconnect
              </button>
              <button className="btn-disconnect" onClick={() => handleDisconnect(provider)}>
                Disconnect
              </button>
            </div>
          </div>
        ) : state.status === 'connecting' ? (
          <div className="integration-card-body">
            <span className="connecting-spinner">Connecting to Google...</span>
          </div>
        ) : (
          <div className="integration-card-body">
            {state.error && <div className="integration-error">{state.error}</div>}
            <button className="btn-oauth google" onClick={() => handleGoogleOAuth(provider)}>
              <GoogleIcon /> Connect Google Account
            </button>
          </div>
        )}
      </div>
    );
  };

  /* ─── Render Auth Modals (Sign In, Sign Up, Forgot Password) ─── */
  const renderAuth = () => {
    /* 1. Forgot Password Flow with OTP */
    if (authMode === 'forgot') {
      return (
        <div className="login-backdrop">
          <div className="login-modal animate-fade-in">
            <div className="login-header">
              <div className="login-logo">
                <svg width="32" height="32" viewBox="0 0 32 32" fill="none">
                  <rect width="32" height="32" rx="8" fill="#3b82f6" />
                  <path d="M16 8v8m0 4h.01" stroke="white" strokeWidth="3" strokeLinecap="round" />
                </svg>
              </div>
              <h2 className="login-title">Reset Password</h2>
              <p className="login-subtitle">
                {forgotStep === 1
                  ? 'Enter your email to receive a 6-digit verification code'
                  : 'Enter the verification code and set your new password'}
              </p>
            </div>

            {loginError && (
              <div className="login-error animate-fade-in">
                <svg width="16" height="16" viewBox="0 0 16 16" fill="currentColor">
                  <path d="M8 1a7 7 0 100 14A7 7 0 008 1zm-.75 4a.75.75 0 011.5 0v3a.75.75 0 01-1.5 0V5zm.75 6.25a.75.75 0 100-1.5.75.75 0 000 1.5z" />
                </svg>
                {loginError}
              </div>
            )}

            {resetMsg && (
              <div
                style={{
                  padding: '0.75rem',
                  marginBottom: '1rem',
                  background: 'rgba(34, 197, 94, 0.15)',
                  border: '1px solid #22c55e',
                  borderRadius: '8px',
                  color: '#15803d',
                  fontSize: '0.85rem',
                  fontWeight: 500,
                  display: 'flex',
                  alignItems: 'center',
                  gap: '0.5rem',
                }}
              >
                <span>✓</span>
                <span>{resetMsg}</span>
              </div>
            )}

            {forgotStep === 1 ? (
              <form onSubmit={handleForgotSendOtp} className="login-form">
                <div className="login-input-group">
                  <input
                    type="email"
                    className="login-input"
                    placeholder="name@company.com"
                    value={email}
                    onChange={(e) => {
                      setEmail(e.target.value);
                      setLoginError('');
                    }}
                    required
                    autoFocus
                    autoComplete="email"
                  />
                </div>
                <button type="submit" className="login-submit-btn" disabled={isLoggingIn}>
                  {isLoggingIn ? <span className="login-spinner" /> : 'Send Verification Code'}
                </button>
              </form>
            ) : (
              <form onSubmit={handleResetPasswordOtp} className="login-form">
                <div className="otp-info-badge">
                  <span>✉️</span>
                  <span>Code sent to <strong>{email}</strong></span>
                </div>

                <div className="login-input-group">
                  <input
                    type="text"
                    className="otp-code-input"
                    placeholder="123456"
                    maxLength={6}
                    value={resetOtp}
                    onChange={(e) => {
                      setResetOtp(e.target.value.replace(/\D/g, ''));
                      setLoginError('');
                    }}
                    required
                    autoFocus
                    autoComplete="one-time-code"
                  />
                </div>

                <div className="login-input-group">
                  <input
                    type="password"
                    className="login-input"
                    placeholder="New password (min 6 characters)"
                    value={newPassword}
                    onChange={(e) => {
                      setNewPassword(e.target.value);
                      setLoginError('');
                    }}
                    required
                    autoComplete="new-password"
                  />
                </div>

                <div className="login-input-group">
                  <input
                    type="password"
                    className="login-input"
                    placeholder="Confirm new password"
                    value={confirmPassword}
                    onChange={(e) => {
                      setConfirmPassword(e.target.value);
                      setLoginError('');
                    }}
                    required
                    autoComplete="new-password"
                  />
                </div>

                <button type="submit" className="login-submit-btn" disabled={isLoggingIn}>
                  {isLoggingIn ? <span className="login-spinner" /> : 'Reset Password'}
                </button>

                <div className="otp-resend-row">
                  <span>Didn't receive code?</span>
                  <button
                    type="button"
                    className="otp-resend-btn"
                    disabled={otpCountdown > 0 || isLoggingIn}
                    onClick={() => handleForgotSendOtp()}
                  >
                    {otpCountdown > 0 ? `Resend in ${otpCountdown}s` : 'Resend Code'}
                  </button>
                </div>

                <button
                  type="button"
                  className="login-back-btn"
                  onClick={() => {
                    setForgotStep(1);
                    setResetOtp('');
                    setNewPassword('');
                    setConfirmPassword('');
                    setLoginError('');
                    setResetMsg('');
                  }}
                >
                  ← Use a different email
                </button>
              </form>
            )}

            <div className="login-footer">
              <span>Remember your password?</span>
              <button
                type="button"
                className="login-toggle-btn"
                onClick={() => {
                  setAuthMode('signin');
                  setAuthMethod('password');
                  setForgotStep(1);
                  setLoginError('');
                  setResetMsg('');
                }}
              >
                Back to Sign In
              </button>
            </div>
          </div>
        </div>
      );
    }

    /* 2. Main Auth Modal (Sign In / Sign Up with Password vs OTP tabs) */
    return (
      <div className="login-backdrop">
        <div className="login-modal animate-fade-in">
          {/* Header */}
          <div className="login-header">
            <div className="login-logo">
              <svg width="32" height="32" viewBox="0 0 32 32" fill="none">
                <rect width="32" height="32" rx="8" fill="#3b82f6" />
                <path d="M8 16L14 22L24 10" stroke="white" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round" />
              </svg>
            </div>
            <h2 className="login-title">
              {authMode === 'signin' ? 'Sign in to DPDP Scanner' : 'Create an Account'}
            </h2>
            <p className="login-subtitle">
              {authMode === 'signin'
                ? 'Choose your preferred sign-in method'
                : 'Get started with automated DPDP assessments'}
            </p>
          </div>

          {/* Error Message */}
          {loginError && (
            <div className="login-error animate-fade-in">
              <svg width="16" height="16" viewBox="0 0 16 16" fill="currentColor">
                <path d="M8 1a7 7 0 100 14A7 7 0 008 1zm-.75 4a.75.75 0 011.5 0v3a.75.75 0 01-1.5 0V5zm.75 6.25a.75.75 0 100-1.5.75.75 0 000 1.5z" />
              </svg>
              {loginError}
            </div>
          )}

          {/* Success / Status Message */}
          {resetMsg && (
            <div
              style={{
                padding: '0.75rem',
                marginBottom: '1rem',
                background: 'rgba(34, 197, 94, 0.15)',
                border: '1px solid #22c55e',
                borderRadius: '8px',
                color: '#15803d',
                fontSize: '0.85rem',
                fontWeight: 500,
                display: 'flex',
                alignItems: 'center',
                gap: '0.5rem',
              }}
            >
              <span>✓</span>
              <span>{resetMsg}</span>
            </div>
          )}

          {/* Google Sign-In Button */}
          <button
            type="button"
            className="login-google-btn"
            onClick={() => googleLogin()}
            disabled={isLoggingIn}
          >
            <GoogleIcon />
            Continue with Google
          </button>

          {/* Divider */}
          <div className="login-divider">
            <span>OR</span>
          </div>

          {/* Mode Tabs: Password vs OTP */}
          <div className="auth-tabs">
            <button
              type="button"
              className={`auth-tab ${authMethod === 'password' ? 'active' : ''}`}
              onClick={() => {
                setAuthMethod('password');
                setLoginError('');
                setResetMsg('');
              }}
            >
              Password Login
            </button>
            <button
              type="button"
              className={`auth-tab ${authMethod === 'otp' ? 'active' : ''}`}
              onClick={() => {
                setAuthMethod('otp');
                setLoginError('');
                setResetMsg('');
                if (email && !otpEmail) setOtpEmail(email);
              }}
            >
              Login via OTP
            </button>
          </div>

          {/* Tab 1: Email & Password */}
          {authMethod === 'password' && (
            <form onSubmit={handlePasswordSubmit} className="login-form animate-fade-in">
              <div className="login-input-group">
                <input
                  type="email"
                  className="login-input"
                  placeholder="Email address"
                  value={email}
                  onChange={(e) => {
                    setEmail(e.target.value);
                    setLoginError('');
                  }}
                  required
                  autoComplete="email"
                />
              </div>

              <div className="login-input-group">
                <input
                  type="password"
                  className="login-input"
                  placeholder={authMode === 'signin' ? 'Password' : 'Password (min 6 characters)'}
                  value={password}
                  onChange={(e) => {
                    setPassword(e.target.value);
                    setLoginError('');
                  }}
                  required
                  autoComplete={authMode === 'signin' ? 'current-password' : 'new-password'}
                />
              </div>

              {authMode === 'signin' && (
                <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: '-0.25rem' }}>
                  <button
                    type="button"
                    className="login-toggle-btn"
                    style={{ fontSize: '0.8rem' }}
                    onClick={() => {
                      setAuthMode('forgot');
                      setForgotStep(1);
                      setLoginError('');
                      setResetMsg('');
                      setResetOtp('');
                      setNewPassword('');
                      setConfirmPassword('');
                    }}
                  >
                    Forgot password?
                  </button>
                </div>
              )}

              <button type="submit" className="login-submit-btn" disabled={isLoggingIn}>
                {isLoggingIn ? (
                  <span className="login-spinner" />
                ) : authMode === 'signin' ? (
                  'Sign In'
                ) : (
                  'Create Account'
                )}
              </button>

              <div className="login-footer">
                <span>{authMode === 'signin' ? "Don't have an account?" : 'Already have an account?'}</span>
                <button
                  type="button"
                  className="login-toggle-btn"
                  onClick={() => {
                    setAuthMode(authMode === 'signin' ? 'signup' : 'signin');
                    setLoginError('');
                    setResetMsg('');
                  }}
                >
                  {authMode === 'signin' ? 'Sign up' : 'Log in'}
                </button>
              </div>
            </form>
          )}

          {/* Tab 2: Login via OTP */}
          {authMethod === 'otp' && (
            <div className="animate-fade-in">
              {!otpSent ? (
                <form onSubmit={handleSendLoginOtp} className="login-form">
                  <div className="login-input-group">
                    <input
                      type="email"
                      className="login-input"
                      placeholder="Enter your email for instant sign-in"
                      value={otpEmail || email}
                      onChange={(e) => {
                        setOtpEmail(e.target.value);
                        setEmail(e.target.value);
                        setLoginError('');
                      }}
                      required
                      autoFocus
                      autoComplete="email"
                    />
                  </div>

                  <p style={{ fontSize: '0.8rem', color: '#6b7280', margin: '0 0 0.5rem 0', lineHeight: 1.4 }}>
                    We'll email you a secure 6-digit verification code. No password required. New users are automatically registered.
                  </p>

                  <button type="submit" className="login-submit-btn" disabled={isLoggingIn}>
                    {isLoggingIn ? <span className="login-spinner" /> : 'Send Login Code'}
                  </button>
                </form>
              ) : (
                <form onSubmit={handleVerifyLoginOtp} className="login-form">
                  <div className="otp-info-badge">
                    <span>✉️</span>
                    <span>Code sent to <strong>{otpEmail}</strong></span>
                  </div>

                  <div className="login-input-group">
                    <input
                      type="text"
                      className="otp-code-input"
                      placeholder="123456"
                      maxLength={6}
                      value={otpCode}
                      onChange={(e) => {
                        setOtpCode(e.target.value.replace(/\D/g, ''));
                        setLoginError('');
                      }}
                      required
                      autoFocus
                      autoComplete="one-time-code"
                    />
                  </div>

                  <button
                    type="submit"
                    className="login-submit-btn"
                    disabled={isLoggingIn || otpCode.length !== 6}
                  >
                    {isLoggingIn ? <span className="login-spinner" /> : 'Verify & Sign In'}
                  </button>

                  <div className="otp-resend-row">
                    <span>Didn't receive code?</span>
                    <button
                      type="button"
                      className="otp-resend-btn"
                      disabled={otpCountdown > 0 || isLoggingIn}
                      onClick={() => handleSendLoginOtp()}
                    >
                      {otpCountdown > 0 ? `Resend in ${otpCountdown}s` : 'Resend Code'}
                    </button>
                  </div>

                  <button
                    type="button"
                    className="login-back-btn"
                    onClick={() => {
                      setOtpSent(false);
                      setOtpCode('');
                      setLoginError('');
                      setResetMsg('');
                    }}
                  >
                    ← Use a different email
                  </button>
                </form>
              )}
            </div>
          )}
        </div>
      </div>
    );
  };

  /* ─── Render Dashboard ─── */
  const renderDashboard = () => (
    <div className="animate-fade-in">
      <div className="grid grid-cols-3 gap-6" style={{ marginBottom: '2rem' }}>
        <StatCard title="Total Assessments" value={assessments.length.toString()} type="info" />
        <StatCard
          title="High Severity Findings"
          value={findings.filter((f) => f.severity === 'high').length || '—'}
          type="danger"
        />
        <StatCard title="Integration Coverage" value={Object.keys(connections).length + '/7'} type="success" />
      </div>
    </div>
  );

  /* ─── Render New Assessment (Configure Integrations Page) ─── */
  const renderNewAssessment = () => (
    <div className="animate-fade-in flex-col gap-6">
      <div style={{ padding: '1.5rem', background: 'var(--glass-bg)', borderRadius: 'var(--radius-lg)' }}>
        <h2 style={{ fontSize: '1.25rem', marginBottom: '1.25rem' }}>Configure Integrations</h2>
        <div className="integration-grid">
          {/* ─── 1. GTM ─── */}
          <GoogleIntegrationCard provider="gtm" title="GTM" resourceLabel="GTM Container" />

          {/* ─── 2. GA4 ─── */}
          <GoogleIntegrationCard provider="ga4" title="GA4" resourceLabel="GA4 Property" />

          {/* ─── 3. Search Console ─── */}
          <GoogleIntegrationCard provider="search_console" title="Search Console" resourceLabel="Property" />

          {/* ─── 4. Google Ads ─── */}
          {(() => {
            const state = getIntState('google_ads');
            const isConnected = state.status === 'connected';
            const isConnectingWithAccount = state.status === 'connecting' && Boolean(state.accountEmail);
            const effectiveAdsAccount = state.resourceName || state.resourceId || adsCustomerId;
            const isEditingAds = editingProvider === 'google_ads';

            return (
              <div className={`integration-card status-${state.status.replace('_', '-')}`}>
                <div className="integration-card-header">
                  <div className="integration-card-title">
                    <div className="integration-icon google">
                      <GoogleIcon />
                    </div>
                    <h4>Google Ads</h4>
                  </div>
                  <StatusBadge status={state.status} />
                </div>

                {isConnected && effectiveAdsAccount && !isEditingAds ? (
                  <ConnectedInfo
                    state={{ ...state, resourceName: effectiveAdsAccount }}
                    onChangeResource={() => setEditingProvider('google_ads')}
                    changeLabel="Change Customer ID"
                    onReconnect={() => {
                      handleDisconnect('google_ads');
                      handleGoogleOAuth('google_ads');
                    }}
                    onDisconnect={() => handleDisconnect('google_ads')}
                  />
                ) : (isConnected && (!effectiveAdsAccount || isEditingAds)) || isConnectingWithAccount ? (
                  <div className="integration-card-body animate-fade-in">
                    <div className="connected-info">
                      <div className="connected-info-row">
                        <span className="connected-info-label">Account:</span>
                        <span className="connected-info-value">{state.accountEmail}</span>
                      </div>
                    </div>
                    <div>
                      <label className="integration-label">Customer ID</label>
                      <input
                        type="text"
                        className="integration-input"
                        placeholder="123-456-7890"
                        value={adsCustomerId}
                        onChange={(e) => setAdsCustomerId(e.target.value)}
                      />
                    </div>
                    <div style={{ display: 'flex', gap: '0.5rem', marginTop: '0.5rem' }}>
                      <button
                        className="btn-connect"
                        disabled={!adsCustomerId}
                        onClick={async () => {
                          const normalizedId = adsCustomerId.replace(/-/g, '');
                          await finalizeConnection('google_ads', { id: normalizedId, name: adsCustomerId });
                          setEditingProvider(null);
                        }}
                      >
                        Save Customer ID
                      </button>
                      {isEditingAds && (
                        <button
                          className="btn-secondary"
                          style={{ fontSize: '0.8rem', padding: '0.4rem 0.8rem' }}
                          onClick={() => setEditingProvider(null)}
                        >
                          Cancel
                        </button>
                      )}
                    </div>
                    <div className="integration-actions" style={{ marginTop: '0.75rem' }}>
                      <button
                        className="btn-reconnect"
                        onClick={() => {
                          handleDisconnect('google_ads');
                          handleGoogleOAuth('google_ads');
                        }}
                      >
                        Reconnect
                      </button>
                      <button className="btn-disconnect" onClick={() => handleDisconnect('google_ads')}>
                        Disconnect
                      </button>
                    </div>
                  </div>
                ) : state.status === 'connecting' ? (
                  <div className="integration-card-body">
                    <span className="connecting-spinner">Authorizing Google Ads...</span>
                  </div>
                ) : (
                  <div className="integration-card-body">
                    {state.error && <div className="integration-error">{state.error}</div>}
                    <button className="btn-oauth google" onClick={() => handleGoogleOAuth('google_ads')}>
                      <GoogleIcon /> Connect Google Account
                    </button>
                  </div>
                )}
              </div>
            );
          })()}

          {/* ─── 5. Meta Business ─── */}
          {(() => {
            const state = getIntState('meta');
            const manualBiz = manualInputs['meta'] || '';
            const isConnected = state.status === 'connected';
            const isConnectingWithAccount = state.status === 'connecting' && Boolean(state.accountEmail);
            const effectiveBiz = state.resourceName || state.resourceId;
            const isEditingMeta = editingProvider === 'meta';

            return (
              <div className={`integration-card status-${state.status.replace('_', '-')}`}>
                <div className="integration-card-header">
                  <div className="integration-card-title">
                    <div className="integration-icon meta">
                      <svg viewBox="0 0 24 24" width="20" height="20" fill="currentColor">
                        <path d="M12 2.04c-5.5 0-10 4.49-10 10.02 0 5 3.66 9.15 8.44 9.9v-7H7.9v-2.9h2.54V9.85c0-2.52 1.49-3.93 3.78-3.93 1.09 0 2.23.2 2.23.2v2.46h-1.26c-1.24 0-1.63.77-1.63 1.56v1.88h2.78l-.45 2.9h-2.33v7a10 10 0 008.44-9.9c0-5.53-4.5-10.02-10-10.02z" />
                      </svg>
                    </div>
                    <h4>Meta Business</h4>
                  </div>
                  <StatusBadge status={state.status} />
                </div>

                {isConnected && effectiveBiz && !isEditingMeta ? (
                  <ConnectedInfo
                    state={{ ...state, resourceName: effectiveBiz }}
                    onChangeResource={() => setEditingProvider('meta')}
                    changeLabel="Change Business"
                    onReconnect={() => {
                      handleDisconnect('meta');
                      handleMetaOAuth();
                    }}
                    onDisconnect={() => handleDisconnect('meta')}
                  />
                ) : (isConnected && (!effectiveBiz || isEditingMeta)) || isConnectingWithAccount ? (
                  <div className="integration-card-body animate-fade-in">
                    <div className="connected-info">
                      <div className="connected-info-row">
                        <span className="connected-info-label">Account:</span>
                        <span className="connected-info-value">{state.accountEmail}</span>
                      </div>
                    </div>
                    {state.resources && state.resources.length > 0 ? (
                      <div>
                        <label className="integration-label">Select Business</label>
                        <select
                          className="integration-select"
                          value={selectedBiz}
                          onChange={(e) => {
                            setSelectedBiz(e.target.value);
                            setSelectedAdAccount('');
                          }}
                        >
                          <option value="">Select Business...</option>
                          {state.resources.map((r) => (
                            <option key={r.id} value={r.id}>
                              {r.name}
                            </option>
                          ))}
                        </select>
                        {selectedBiz && metaAssets.length > 0 && (
                          <div style={{ marginTop: '0.75rem', fontSize: '0.85rem', color: 'var(--text-muted)' }}>
                            Discovered {metaAssets.length} ad accounts. The entire business will be scanned.
                          </div>
                        )}
                      </div>
                    ) : (
                      <div>
                        <label className="integration-label">Business ID</label>
                        <input
                          type="text"
                          className="integration-input"
                          placeholder="Enter Business Account ID"
                          value={manualBiz}
                          onChange={(e) => setManualInputs((prev) => ({ ...prev, meta: e.target.value }))}
                        />
                      </div>
                    )}
                    <div style={{ display: 'flex', gap: '0.5rem', marginTop: '0.5rem' }}>
                      <button
                        className="btn-connect"
                        disabled={!selectedBiz && !manualBiz}
                        onClick={async () => {
                          const foundBiz = (state.resources || []).find((r) => r.id === selectedBiz);
                          let finalRes = null;
                          if (foundBiz) {
                            finalRes = { id: foundBiz.id, name: foundBiz.name };
                          } else if (manualBiz) {
                            finalRes = { id: manualBiz, name: `Business ${manualBiz}` };
                          }
                          if (finalRes) {
                            console.log(`Meta selected business: ${finalRes.id}`);
                            console.log(`Meta resource selection request:`, { provider: 'meta', resource_id: finalRes.id, resource_name: finalRes.name, extra: { business_id: finalRes.id } });
                            await finalizeConnection('meta', finalRes, { business_id: finalRes.id });
                            setEditingProvider(null);
                          }
                        }}
                      >
                        Save
                      </button>
                      {isEditingMeta && (
                        <button
                          className="btn-secondary"
                          style={{ fontSize: '0.8rem', padding: '0.4rem 0.8rem' }}
                          onClick={() => setEditingProvider(null)}
                        >
                          Cancel
                        </button>
                      )}
                    </div>
                    <div className="integration-actions" style={{ marginTop: '0.75rem' }}>
                      <button
                        className="btn-reconnect"
                        onClick={async () => {
                          await handleDisconnect('meta');
                          await handleMetaOAuth();
                        }}
                      >
                        Reconnect
                      </button>
                      <button className="btn-disconnect" onClick={() => handleDisconnect('meta')}>
                        Disconnect
                      </button>
                    </div>
                  </div>
                ) : state.status === 'connecting' ? (
                  <div className="integration-card-body">
                    <span className="connecting-spinner">Connecting to Meta...</span>
                  </div>
                ) : (
                  <div className="integration-card-body">
                    {state.error && <div className="integration-error">{state.error}</div>}
                    <button className="btn-oauth meta" onClick={handleMetaOAuth}>
                      <svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor">
                        <path d="M12 2.04c-5.5 0-10 4.49-10 10.02 0 5 3.66 9.15 8.44 9.9v-7H7.9v-2.9h2.54V9.85c0-2.52 1.49-3.93 3.78-3.93 1.09 0 2.23.2 2.23.2v2.46h-1.26c-1.24 0-1.63.77-1.63 1.56v1.88h2.78l-.45 2.9h-2.33v7a10 10 0 008.44-9.9c0-5.53-4.5-10.02-10-10.02z" />
                      </svg>
                      Connect Meta Business
                    </button>
                  </div>
                )}
              </div>
            );
          })()}

          {/* ─── 6. GitHub ─── */}
          {(() => {
            const state = getIntState('github');
            const isConnected = state.status === 'connected';
            const isConnectingWithAccount = state.status === 'connecting' && Boolean(state.accountName);
            const effectiveRepo = state.resourceName || state.resourceId || selectedRepo;
            const isEditingGithub = editingProvider === 'github';

            return (
              <div className={`integration-card status-${state.status.replace('_', '-')}`}>
                <div className="integration-card-header">
                  <div className="integration-card-title">
                    <div className="integration-icon github">
                      <svg viewBox="0 0 24 24" width="20" height="20" fill="currentColor">
                        <path d="M12 2C6.477 2 2 6.484 2 12.017c0 4.425 2.865 8.18 6.839 9.504.5.092.682-.217.682-.483 0-.237-.008-.868-.013-1.703-2.782.605-3.369-1.343-3.369-1.343-.454-1.158-1.11-1.466-1.11-1.466-.908-.62.069-.608.069-.608 1.003.07 1.531 1.032 1.531 1.032.892 1.53 2.341 1.088 2.91.832.092-.647.35-1.088.636-1.338-2.22-.253-4.555-1.113-4.555-4.951 0-1.093.39-1.988 1.029-2.688-.103-.253-.446-1.272.098-2.65 0 0 .84-.27 2.75 1.026A9.564 9.564 0 0112 6.844c.85.004 1.705.115 2.504.337 1.909-1.296 2.747-1.027 2.747-1.027.546 1.379.202 2.398.1 2.651.64.7 1.028 1.595 1.028 2.688 0 3.848-2.339 4.695-4.566 4.943.359.309.678.92.678 1.855 0 1.338-.012 2.419-.012 2.747 0 .268.18.58.688.482A10.019 10.019 0 0022 12.017C22 6.484 17.522 2 12 2z" />
                      </svg>
                    </div>
                    <h4>GitHub</h4>
                  </div>
                  <StatusBadge status={state.status} />
                </div>

                {isConnected && effectiveRepo && !isEditingGithub ? (
                  <ConnectedInfo
                    state={{ ...state, resourceName: effectiveRepo }}
                    onChangeResource={() => setEditingProvider('github')}
                    changeLabel="Change Repository"
                    onReconnect={() => {
                      handleDisconnect('github');
                      handleGitHubAuth();
                    }}
                    onDisconnect={() => handleDisconnect('github')}
                  />
                ) : (isConnected && (!effectiveRepo || isEditingGithub)) || isConnectingWithAccount ? (
                  <div className="integration-card-body animate-fade-in">
                    {/* ── Select Repo to Scan Dialog ── */}
                    <div style={{
                      background: 'linear-gradient(135deg, rgba(110,64,170,0.08), rgba(36,198,220,0.06))',
                      border: '1px solid rgba(110,64,170,0.2)',
                      borderRadius: '12px',
                      padding: '1.25rem',
                      marginBottom: '0.75rem',
                    }}>
                      <div style={{
                        display: 'flex',
                        alignItems: 'center',
                        gap: '0.5rem',
                        marginBottom: '1rem',
                      }}>
                        <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="var(--accent-primary)" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                          <path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z" />
                        </svg>
                        <span style={{
                          fontWeight: 600,
                          fontSize: '0.95rem',
                          color: 'var(--text-primary)',
                          letterSpacing: '0.02em',
                        }}>
                          Select Repo to Scan
                        </span>
                      </div>

                      <div className="connected-info" style={{ marginBottom: '0.75rem' }}>
                        <div className="connected-info-row">
                          <span className="connected-info-label">Account:</span>
                          <span className="connected-info-value">{state.accountName || 'GitHub App'}</span>
                        </div>
                      </div>

                      {/* Option 1: Dropdown from fetched repos */}
                      {state.resources ? (
                        state.resources.length > 0 ? (
                          <div style={{ marginBottom: '0.75rem' }}>
                            <label className="integration-label" style={{
                              display: 'flex',
                              alignItems: 'center',
                              gap: '0.4rem',
                              marginBottom: '0.4rem',
                            }}>
                              <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                                <polyline points="6 9 12 15 18 9" />
                              </svg>
                              Select from your repositories
                            </label>
                            <select
                              className="integration-select"
                              value={selectedRepo}
                              onChange={(e) => {
                                setSelectedRepo(e.target.value);
                              }}
                              style={{
                                width: '100%',
                                padding: '0.6rem 0.75rem',
                                borderRadius: '8px',
                                border: '1px solid rgba(255,255,255,0.12)',
                                background: 'rgba(0,0,0,0.2)',
                                color: 'var(--text-primary)',
                                fontSize: '0.875rem',
                              }}
                            >
                              <option value="">Select Repository...</option>
                              {state.resources.map((r) => (
                                <option key={r.id} value={r.id}>
                                  {r.name}{r.metadata?.private ? ' 🔒' : ''}
                                </option>
                              ))}
                            </select>
                          </div>
                        ) : (
                          <div style={{ color: 'var(--text-secondary)', fontSize: '0.85rem', padding: '0.25rem 0 0.5rem' }}>
                            No repositories found via GitHub App. Ensure the App is installed with repository access.
                          </div>
                        )
                      ) : (
                        <div style={{
                          display: 'flex',
                          alignItems: 'center',
                          gap: '0.5rem',
                          color: 'var(--text-secondary)',
                          fontSize: '0.85rem',
                          padding: '0.25rem 0 0.5rem',
                        }}>
                          <span className="connecting-spinner" style={{ fontSize: '0.85rem' }}>
                            Loading repositories from GitHub App...
                          </span>
                        </div>
                      )}
                    </div>

                    {/* Action buttons — only from installation-scoped dropdown */}
                    <div style={{ display: 'flex', gap: '0.5rem', marginTop: '0.5rem' }}>
                      <button
                        className="btn-connect"
                        disabled={!selectedRepo}
                        onClick={async () => {
                          if (!selectedRepo) return;
                          const found = (state.resources || []).find((r) => r.id === selectedRepo);
                          const finalRes = found || { id: selectedRepo, name: selectedRepo };
                          if (finalRes && finalRes.id) {
                            await finalizeConnection('github', finalRes, {});
                            setEditingProvider(null);
                          }
                        }}
                        style={{
                          flex: 1,
                          display: 'flex',
                          alignItems: 'center',
                          justifyContent: 'center',
                          gap: '0.5rem',
                        }}
                      >
                        <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                          <polyline points="20 6 9 17 4 12" />
                        </svg>
                        Confirm & Scan
                      </button>
                      {isEditingGithub && (
                        <button
                          className="btn-secondary"
                          style={{ fontSize: '0.8rem', padding: '0.4rem 0.8rem' }}
                          onClick={() => setEditingProvider(null)}
                        >
                          Cancel
                        </button>
                      )}
                    </div>
                    <div className="integration-actions" style={{ marginTop: '0.75rem' }}>
                      <button
                        className="btn-reconnect"
                        onClick={() => {
                          handleDisconnect('github');
                          handleGitHubAuth();
                        }}
                      >
                        Reconnect
                      </button>
                      <button className="btn-disconnect" onClick={() => handleDisconnect('github')}>
                        Disconnect
                      </button>
                    </div>
                  </div>
                ) : state.status === 'connecting' ? (
                  <div className="integration-card-body">
                    <span className="connecting-spinner">Connecting to GitHub...</span>
                  </div>
                ) : (
                  <div className="integration-card-body">
                    {state.error && <div className="integration-error">{state.error}</div>}
                    <p style={{ color: 'var(--text-secondary)', fontSize: '0.85rem', margin: '0 0 0.75rem' }}>
                      Connect via GitHub App to scan repositories for compliance issues.
                      The App requests read-only access to selected repositories only.
                    </p>
                    <button className="btn-oauth github" onClick={handleGitHubAuth}>
                      <svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor">
                        <path d="M12 2C6.477 2 2 6.484 2 12.017c0 4.425 2.865 8.18 6.839 9.504.5.092.682-.217.682-.483 0-.237-.008-.868-.013-1.703-2.782.605-3.369-1.343-3.369-1.343-.454-1.158-1.11-1.466-1.11-1.466-.908-.62.069-.608.069-.608 1.003.07 1.531 1.032 1.531 1.032.892 1.53 2.341 1.088 2.91.832.092-.647.35-1.088.636-1.338-2.22-.253-4.555-1.113-4.555-4.951 0-1.093.39-1.988 1.029-2.688-.103-.253-.446-1.272.098-2.65 0 0 .84-.27 2.75 1.026A9.564 9.564 0 0112 6.844c.85.004 1.705.115 2.504.337 1.909-1.296 2.747-1.027 2.747-1.027.546 1.379.202 2.398.1 2.651.64.7 1.028 1.595 1.028 2.688 0 3.848-2.339 4.695-4.566 4.943.359.309.678.92.678 1.855 0 1.338-.012 2.419-.012 2.747 0 .268.18.58.688.482A10.019 10.019 0 0022 12.017C22 6.484 17.522 2 12 2z" />
                      </svg>
                      Connect GitHub
                    </button>
                  </div>
                )}
              </div>
            );
          })()}

          {/* ─── 7. CMP ─── */}
          {(() => {
            const state = getIntState('cmp');
            return (
              <div className={`integration-card full-width status-${state.status.replace('_', '-')}`}>
                <div className="integration-card-header">
                  <div className="integration-card-title">
                    <div className="integration-icon cmp">
                      <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                        <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" />
                      </svg>
                    </div>
                    <h4>CMP (Consent Management Platform)</h4>
                  </div>
                  <StatusBadge status={state.status} />
                </div>

                {state.status === 'connected' ? (
                  <ConnectedInfo
                    state={state}
                    onReconnect={() => handleDisconnect('cmp')}
                    onDisconnect={() => handleDisconnect('cmp')}
                  />
                ) : (
                  <div className="integration-card-body">
                    {state.error && <div className="integration-error">{state.error}</div>}
                    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: '0.75rem' }}>
                      <div>
                        <label className="integration-label">Provider</label>
                        <select
                          className="integration-select"
                          value={cmpProvider}
                          onChange={(e) => setCmpProvider(e.target.value)}
                        >
                          <option value="OneTrust">OneTrust</option>
                          <option value="CookieYes">CookieYes</option>
                          <option value="Securiti">Securiti</option>
                        </select>
                      </div>
                      <div>
                        <label className="integration-label">API Key</label>
                        <input
                          type="password"
                          className="integration-input"
                          placeholder="Provider API Key"
                          value={cmpKey}
                          onChange={(e) => setCmpKey(e.target.value)}
                        />
                      </div>
                      <div>
                        <label className="integration-label">Site / Property</label>
                        <input
                          type="text"
                          className="integration-input"
                          placeholder="Site ID or URL"
                          value={cmpSite}
                          onChange={(e) => setCmpSite(e.target.value)}
                        />
                      </div>
                    </div>
                    <button
                      className="btn-connect"
                      style={{ alignSelf: 'flex-end', maxWidth: '200px' }}
                      disabled={!cmpKey || !cmpSite}
                      onClick={() =>
                        finalizeConnection('cmp', null, {
                          provider_name: cmpProvider.toLowerCase(),
                          oauth_code: cmpKey,
                          site_id: cmpSite,
                          site_name: `${cmpProvider} - ${cmpSite}`,
                        })
                      }
                    >
                      Connect CMP
                    </button>
                  </div>
                )}
              </div>
            );
          })()}
        </div>

        <div
          style={{
            marginTop: '2rem',
            display: 'flex',
            justifyContent: 'flex-end',
            borderTop: '1px solid var(--border-color)',
            paddingTop: '1rem',
          }}
        >
          <button
            className="btn btn-primary"
            onClick={handleStartAssessment}
            disabled={
              loading ||
              Object.keys(connections).filter((k) => (connections[k] as any)?.status === 'connected').length === 0
            }
          >
            {loading ? 'Submitting...' : 'Submit Assessment'}
          </button>
        </div>
      </div>
    </div>
  );

  /* ─── Render Status Report ─── */
  const renderStatusReport = () => {
    if (!activeAssessment && !activeAssessmentId) {
      return (
        <div className="card glass-panel text-center" style={{ padding: '3rem 1.5rem' }}>
          <h3>No Active Assessment Selected</h3>
          <p style={{ color: 'var(--text-secondary)', margin: '1rem 0 1.5rem' }}>
            Select an assessment from the Assessment History tab or create a new assessment to inspect findings.
          </p>
          <div className="flex justify-center gap-4">
            <button className="btn btn-primary" onClick={() => setActiveTab('assessments')}>
              View Assessment History
            </button>
            <button className="btn btn-secondary" onClick={() => setActiveTab('new_assessment')}>
              + New Assessment
            </button>
          </div>
        </div>
      );
    }

    const isDone = activeAssessment?.status === 'completed' || activeAssessment?.status === 'partial';
    const indReports = reportData?.individual_reports || {};
    const integrationKeys = Object.keys(indReports);

    // Build tab list: Combined + any detected integration reports
    const allIntegrationTabs: { key: string; name: string; badge: number }[] = [
      { key: 'combined', name: 'Combined Overall Report', badge: findings.length },
    ];

    if (integrationKeys.length > 0) {
      integrationKeys.forEach((k) => {
        const rep = indReports[k];
        allIntegrationTabs.push({
          key: k,
          name: rep.integration_name || rep.title || k.toUpperCase(),
          badge: rep.findings?.length || 0,
        });
      });
    } else {
      const discoveredKeys = new Set<string>();
      findings.forEach((f) => {
        (f.sources || []).forEach((s: string) => {
          const k = s.split(':')[0].toLowerCase();
          if (['gtm', 'ga4', 'google_ads', 'search_console', 'meta', 'github', 'cmp'].includes(k)) {
            discoveredKeys.add(k);
          }
        });
      });
      discoveredKeys.forEach((k) => {
        const count = findings.filter((f) => (f.sources || []).some((s: string) => s.toLowerCase().startsWith(k))).length;
        allIntegrationTabs.push({
          key: k,
          name: k === 'gtm' ? 'Google Tag Manager' : k === 'ga4' ? 'Google Analytics 4' : k === 'google_ads' ? 'Google Ads' : k === 'search_console' ? 'Google Search Console' : k.toUpperCase(),
          badge: count,
        });
      });
    }

    const isCombined = reportIntegrationTab === 'combined';
    const activeIndRep = indReports[reportIntegrationTab];

    const currentFindings = isCombined
      ? findings
      : (activeIndRep?.findings || findings.filter((f: any) => (f.sources || []).some((s: string) => s.toLowerCase().startsWith(reportIntegrationTab))));

    const curTotal = currentFindings.length;
    const curHigh = currentFindings.filter((f: any) => (f.severity || '').toLowerCase() === 'high').length;
    const curMed = currentFindings.filter((f: any) => (f.severity || '').toLowerCase() === 'medium').length;
    const curLow = currentFindings.filter((f: any) => (f.severity || '').toLowerCase() === 'low').length;
    const curObserved = currentFindings.filter((f: any) => (f.verdict || '').toLowerCase() === 'observed').length;
    const curNotObserved = currentFindings.filter((f: any) => (f.verdict || '').toLowerCase() === 'not_observed' || f.verdict === 'Not observed').length;
    const curCannot = currentFindings.filter((f: any) => (f.verdict || '').toLowerCase() === 'cannot_determine' || f.verdict === 'Cannot determine').length;

    const currentTitle = isCombined
      ? 'Digital Personal Data Protection (DPDP) — Comprehensive Assessment Report'
      : (activeIndRep?.title || `${allIntegrationTabs.find((t) => t.key === reportIntegrationTab)?.name || reportIntegrationTab.toUpperCase()} Assessment Report`);

    const currentResource = !isCombined && activeIndRep?.connection_metadata?.resource_name
      ? activeIndRep.connection_metadata.resource_name
      : null;

    return (
      <div className="animate-fade-in flex-col gap-6">
        {/* Top Status & Assessment Metadata */}
        <div className="card glass-panel">
          <div className="flex justify-between items-center" style={{ flexWrap: 'wrap', gap: '1rem' }}>
            <div>
              <h3 style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', fontSize: '1.15rem' }}>
                Assessment Status:
                <span className={`badge ${isDone ? 'badge-success' : activeAssessment?.status === 'failed' ? 'badge-danger' : 'badge-warning'}`}>
                  {activeAssessment?.status || 'Processing'}
                </span>
              </h3>
              <div style={{ marginTop: '0.5rem', fontSize: '0.875rem', color: 'var(--text-secondary)' }}>
                <span>ID: <code>{activeAssessmentId}</code></span> | 
                <span style={{ marginLeft: '0.5rem' }}>
                  Date: {activeAssessment?.created_at ? new Date(activeAssessment.created_at).toLocaleString() : 'Just now'}
                </span>
              </div>
            </div>
            {isDone && (
              <div className="flex gap-2">
                <button
                  className="btn btn-secondary"
                  onClick={() => handleExportJSON(reportIntegrationTab)}
                  title="Download report JSON"
                >
                  Export JSON ({isCombined ? 'Combined' : reportIntegrationTab.toUpperCase()})
                </button>
                <button
                  className="btn btn-primary"
                  onClick={() => handleExportPDF(reportIntegrationTab)}
                  title="Open styled printable PDF report"
                >
                  Export PDF ({isCombined ? 'Combined' : reportIntegrationTab.toUpperCase()})
                </button>
              </div>
            )}
          </div>
        </div>

        {/* Report Integration Selector Tabs */}
        {isDone && (
          <div className="card glass-panel" style={{ padding: '0.75rem 1rem' }}>
            <div className="flex gap-2" style={{ overflowX: 'auto', paddingBottom: '0.25rem' }}>
              {allIntegrationTabs.map((t) => {
                const isSelected = reportIntegrationTab === t.key;
                return (
                  <button
                    key={t.key}
                    onClick={() => setReportIntegrationTab(t.key)}
                    style={{
                      background: isSelected ? 'var(--accent-primary)' : 'rgba(255, 255, 255, 0.05)',
                      color: isSelected ? '#fff' : 'var(--text-secondary)',
                      border: isSelected ? '1px solid var(--accent-primary)' : '1px solid var(--border-color)',
                      borderRadius: 'var(--radius-md)',
                      padding: '0.6rem 1rem',
                      fontWeight: isSelected ? 600 : 400,
                      cursor: 'pointer',
                      display: 'flex',
                      alignItems: 'center',
                      gap: '0.5rem',
                      whiteSpace: 'nowrap',
                      transition: 'all 0.2s ease',
                    }}
                  >
                    <span>{t.name}</span>
                    <span
                      style={{
                        background: isSelected ? 'rgba(255, 255, 255, 0.25)' : 'rgba(255, 255, 255, 0.1)',
                        padding: '2px 6px',
                        borderRadius: '999px',
                        fontSize: '0.75rem',
                      }}
                    >
                      {t.badge}
                    </span>
                  </button>
                );
              })}
            </div>
          </div>
        )}

        {/* Selected Report Content Card */}
        {isDone && (
          <div className="card glass-panel flex-col gap-6">
            <div className="flex justify-between items-center" style={{ borderBottom: '1px solid var(--border-color)', paddingBottom: '1rem' }}>
              <div>
                <h2 style={{ fontSize: '1.35rem', color: 'var(--text-primary)', margin: 0 }}>
                  {currentTitle}
                </h2>
                {currentResource && (
                  <p style={{ color: 'var(--accent-primary)', fontSize: '0.875rem', marginTop: '0.25rem' }}>
                    Target Resource: <strong>{currentResource}</strong>
                  </p>
                )}
              </div>
              <div className="flex gap-2">
                <span className="badge badge-success">Observed: {curObserved}</span>
                <span className="badge badge-danger">Not Observed: {curNotObserved}</span>
                <span className="badge badge-info">Cannot Determine: {curCannot}</span>
              </div>
            </div>

            {/* Metrics Breakdown */}
            <div className="grid grid-cols-4 gap-4">
              <StatCard title="Total Findings" value={curTotal} subtitle={`${isCombined ? 'All Integrations' : 'Single Integration'}`} type="info" />
              <StatCard title="High Severity" value={curHigh} subtitle="Immediate Action" type="danger" />
              <StatCard title="Medium Severity" value={curMed} subtitle="Compliance Review" type="warning" />
              <StatCard title="Low Severity" value={curLow} subtitle="Informational" type="info" />
            </div>

            {/* Findings List */}
            <div className="flex-col gap-4">
              <h3 style={{ fontSize: '1.1rem', color: 'var(--text-primary)' }}>
                Detailed Findings ({currentFindings.length})
              </h3>
              {currentFindings.map((f: any, i: number) => {
                const verdict = strVerdict(f.verdict);
                const sev = (f.severity || 'medium').toLowerCase();
                const code = f.obligation_code || f.obligation_id;
                const title = f.obligation_title || '';

                return (
                  <div
                    key={f.id || i}
                    style={{
                      padding: '1.25rem',
                      border: '1px solid var(--border-color)',
                      borderRadius: 'var(--radius-md)',
                      backgroundColor: 'rgba(255, 255, 255, 0.02)',
                    }}
                  >
                    <div className="flex justify-between items-start" style={{ marginBottom: '0.5rem' }}>
                      <div>
                        <strong style={{ fontSize: '1rem', color: 'var(--text-primary)' }}>
                          {code}{title ? `: ${title}` : ''}
                        </strong>
                        {f.dpdp_section && (
                          <span
                            className="badge"
                            style={{
                              marginLeft: '0.5rem',
                              backgroundColor: 'rgba(59, 130, 246, 0.15)',
                              color: '#60a5fa',
                              fontSize: '0.75rem',
                            }}
                          >
                            {f.dpdp_section}
                          </span>
                        )}
                      </div>
                      <span className={`badge badge-${sev === 'high' ? 'danger' : sev === 'medium' ? 'warning' : 'info'}`}>
                        {sev}
                      </span>
                    </div>

                    <div style={{ margin: '0.75rem 0', color: 'var(--text-primary)', lineHeight: 1.6, fontSize: '0.95rem' }}>
                      <div style={{ marginBottom: '0.75rem' }}>
                        <h5 style={{ fontSize: '0.85rem', color: 'var(--text-secondary)', marginBottom: '0.25rem' }}>Technical Observation:</h5>
                        <p style={{ margin: 0 }}>{f.detail}</p>
                      </div>
                      
                      <div style={{ marginBottom: '0.75rem', background: 'rgba(59, 130, 246, 0.05)', padding: '0.5rem', borderRadius: '4px', borderLeft: '2px solid #3b82f6' }}>
                        <h5 style={{ fontSize: '0.85rem', color: '#60a5fa', marginBottom: '0.25rem' }}>Compliance Implication:</h5>
                        <p style={{ margin: 0, fontSize: '0.9rem' }}>{f.compliance_implication || 'Pending legal review to determine compliance implications.'}</p>
                      </div>
                    </div>

                    <div
                      style={{
                        marginBottom: '0.75rem',
                        padding: '0.5rem 0.75rem',
                        borderRadius: '4px',
                        background: 'rgba(168, 85, 247, 0.1)',
                        border: '1px solid rgba(168, 85, 247, 0.3)',
                        color: '#c084fc',
                        fontSize: '0.8rem',
                      }}
                    >
                      <span>⚖️ <strong>Legal Review Required:</strong> The current obligation mapping is a draft pending legal review. Technical findings do not automatically constitute a legal violation.</span>
                    </div>

                    {f.human_review_required && f.status !== 'OVERRIDDEN' && (
                      <div
                        style={{
                          marginBottom: '0.75rem',
                          padding: '0.5rem 0.75rem',
                          borderRadius: '4px',
                          background: 'rgba(245, 158, 11, 0.1)',
                          border: '1px solid rgba(245, 158, 11, 0.3)',
                          color: '#fbbf24',
                          fontSize: '0.8rem',
                          display: 'flex',
                          justifyContent: 'space-between',
                          alignItems: 'center'
                        }}
                      >
                        <span>⚠️ Human Review Required: Conflicting signals detected between sources.</span>
                        <button 
                          className="btn btn-secondary" 
                          style={{ padding: '0.25rem 0.5rem', fontSize: '0.75rem' }}
                          onClick={() => {
                            setOverrideFinding(f);
                            setOverrideModalOpen(true);
                          }}
                        >
                          Review & Override
                        </button>
                      </div>
                    )}

                    {f.status === 'OVERRIDDEN' && (
                      <div
                        style={{
                          marginBottom: '0.75rem',
                          padding: '0.5rem 0.75rem',
                          borderRadius: '4px',
                          background: 'rgba(16, 185, 129, 0.1)',
                          border: '1px solid rgba(16, 185, 129, 0.3)',
                          color: '#10b981',
                          fontSize: '0.8rem',
                        }}
                      >
                        ✓ Human Reviewed
                        <div style={{ marginTop: '0.25rem', fontSize: '0.75rem', opacity: 0.8 }}>
                          Original: <strong>{f.original_verdict}</strong> ➔ New: <strong>{f.verdict}</strong><br/>
                          Reason: {f.override_reason}
                        </div>
                      </div>
                    )}

                    {f.evidence_details && f.evidence_details.length > 0 && (
                      <div style={{ marginTop: '1rem', marginBottom: '1rem', borderTop: '1px solid rgba(255, 255, 255, 0.1)', paddingTop: '1rem' }}>
                        <h4 style={{ fontSize: '0.9rem', marginBottom: '0.5rem', color: 'var(--text-secondary)' }}>Evidence Trace</h4>
                        <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
                          {f.evidence_details.map((ev: any, evIdx: number) => {
                            if (ev.source === 'reconciliation') {
                              return (
                                <div key={evIdx} style={{ background: 'rgba(0,0,0,0.2)', padding: '0.75rem', borderRadius: '4px', borderLeft: '2px solid var(--primary-color)' }}>
                                  <div style={{ fontSize: '0.8rem', color: 'var(--text-secondary)', marginBottom: '0.25rem' }}>
                                    <strong>Source A:</strong> {ev.metadata?.source_a} | <strong>Source B:</strong> {ev.metadata?.source_b}
                                  </div>
                                  <div style={{ fontSize: '0.85rem', marginBottom: '0.25rem' }}>
                                    <strong>Resource:</strong> Cross-Source Check ({ev.operation})
                                  </div>
                                  <div style={{ fontSize: '0.85rem', marginBottom: '0.25rem' }}>
                                    <strong>Observation:</strong> {ev.detail}
                                  </div>
                                  <div style={{ fontSize: '0.8rem', color: '#fbbf24' }}>
                                    <strong>Result:</strong> {ev.reason || 'reconciliation/contradiction'}
                                  </div>
                                </div>
                              );
                            }
                            return (
                              <div key={evIdx} style={{ background: 'rgba(0,0,0,0.2)', padding: '0.75rem', borderRadius: '4px', borderLeft: '2px solid rgba(255,255,255,0.2)' }}>
                                <div style={{ fontSize: '0.8rem', color: 'var(--text-secondary)', marginBottom: '0.25rem' }}>
                                  <strong>Source:</strong> {ev.integration || ev.source} {ev.operation ? `(${ev.operation})` : ''}
                                </div>
                                <div style={{ fontSize: '0.85rem', marginBottom: '0.25rem' }}>
                                  <strong>Resource:</strong> {ev.resource_id || ev.resource_name || 'N/A'} {ev.location ? `[${ev.location}]` : ''}
                                </div>
                                <div style={{ fontSize: '0.85rem', marginBottom: '0.25rem' }}>
                                  <strong>Observation:</strong> {ev.detail}
                                </div>
                                <div style={{ fontSize: '0.8rem', color: 'var(--text-secondary)', display: 'flex', gap: '1rem' }}>
                                  <span><strong>Observed:</strong> {ev.observed_state || 'N/A'}</span>
                                  <span><strong>Expected:</strong> {ev.expected_state || 'N/A'}</span>
                                </div>
                              </div>
                            );
                          })}
                        </div>
                      </div>
                    )}

                    <div className="flex justify-between items-center" style={{ fontSize: '0.8rem', color: 'var(--text-secondary)', borderTop: '1px solid rgba(255, 255, 255, 0.05)', paddingTop: '0.5rem' }}>
                      <div>
                        <strong>Verdict: </strong>
                        <span
                          style={{
                            fontWeight: 600,
                            color: verdict.toLowerCase().includes('observed') && !verdict.toLowerCase().includes('not')
                              ? '#10b981'
                              : verdict.toLowerCase().includes('not')
                              ? '#ef4444'
                              : 'var(--text-secondary)',
                          }}
                        >
                          {verdict}
                        </span> | 
                        <strong style={{ marginLeft: '0.5rem' }}>Confidence: </strong>{f.confidence}
                      </div>
                      <div>
                        <strong>Sources: </strong>
                        {f.sources && f.sources.length > 0 ? f.sources.join(', ') : 'Direct evaluation'}
                      </div>
                    </div>
                  </div>
                );
              })}

              {currentFindings.length === 0 && (
                <div className="text-center" style={{ padding: '2rem', color: 'var(--text-secondary)' }}>
                  No findings recorded for this integration.
                </div>
              )}
            </div>

            {/* Cross-Source Reconciliation Section in Combined View */}
            {isCombined && reportData?.reconciliations && reportData.reconciliations.length > 0 && (
              <div style={{ marginTop: '1.5rem', borderTop: '1px solid var(--border-color)', paddingTop: '1.5rem' }}>
                <h3 style={{ fontSize: '1.1rem', marginBottom: '1rem', color: 'var(--text-primary)' }}>
                  Cross-Source Reconciliation & Conflict Analysis ({reportData.reconciliations.length})
                </h3>
                <div style={{ overflowX: 'auto' }}>
                  <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.875rem' }}>
                    <thead>
                      <tr style={{ borderBottom: '1px solid var(--border-color)', textAlign: 'left' }}>
                        <th style={{ padding: '0.75rem' }}>Rule ID</th>
                        <th style={{ padding: '0.75rem' }}>Source A</th>
                        <th style={{ padding: '0.75rem' }}>Source B</th>
                        <th style={{ padding: '0.75rem' }}>Outcome</th>
                        <th style={{ padding: '0.75rem' }}>Observation Detail</th>
                      </tr>
                    </thead>
                    <tbody>
                      {reportData.reconciliations.map((r: any, idx: number) => {
                        const isContradiction = (r.outcome || '').toLowerCase() === 'contradiction';
                        return (
                          <tr key={idx} style={{ borderBottom: '1px solid rgba(255, 255, 255, 0.05)' }}>
                            <td style={{ padding: '0.75rem', fontFamily: 'monospace' }}>{r.rule_id}</td>
                            <td style={{ padding: '0.75rem' }}>{r.source_a}</td>
                            <td style={{ padding: '0.75rem' }}>{r.source_b}</td>
                            <td style={{ padding: '0.75rem' }}>
                              <span className={`badge ${isContradiction ? 'badge-danger' : 'badge-success'}`}>
                                {r.outcome}
                              </span>
                            </td>
                            <td style={{ padding: '0.75rem', color: 'var(--text-secondary)' }}>{r.detail}</td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              </div>
            )}
          </div>
        )}
      </div>
    );
  };

  /* ─── Helper to Stringify Verdict ─── */
  const strVerdict = (v: any): string => {
    if (!v) return 'Cannot determine';
    if (typeof v === 'object' && v.value) return v.value;
    return String(v);
  };

  /* ─── Render Assessment History ─── */
  const renderAssessmentHistory = () => {
    return (
      <div className="animate-fade-in flex-col gap-6">
        <div className="flex justify-between items-center" style={{ flexWrap: 'wrap', gap: '1rem' }}>
          <div>
            <h2 style={{ fontSize: '1.25rem', color: 'var(--text-primary)', margin: 0 }}>
              Assessment History
            </h2>
            <p style={{ color: 'var(--text-secondary)', fontSize: '0.875rem', marginTop: '0.25rem' }}>
              Inspect past compliance assessments, review individual and combined reports, and export evidence artifacts.
            </p>
          </div>
          <div className="flex gap-2">
            <button
              className="btn btn-secondary"
              onClick={() => token && fetchAssessments(token)}
            >
              ↻ Refresh
            </button>
            <button
              className="btn btn-primary"
              onClick={() => setActiveTab('new_assessment')}
            >
              + New Assessment
            </button>
          </div>
        </div>

        {assessments.length === 0 ? (
          <div className="card glass-panel text-center" style={{ padding: '3rem 1.5rem' }}>
            <h3 style={{ marginBottom: '0.5rem' }}>No Past Assessments Found</h3>
            <p style={{ color: 'var(--text-secondary)', marginBottom: '1.5rem' }}>
              You haven't conducted any assessments yet. Connect your integrations and submit an assessment to generate your compliance history.
            </p>
            <button className="btn btn-primary" onClick={() => setActiveTab('new_assessment')}>
              Create First Assessment
            </button>
          </div>
        ) : (
          <div className="card glass-panel" style={{ padding: '0', overflow: 'hidden' }}>
            <div style={{ overflowX: 'auto' }}>
              <table style={{ width: '100%', borderCollapse: 'collapse', textAlign: 'left' }}>
                <thead>
                  <tr style={{ borderBottom: '1px solid var(--border-color)', backgroundColor: 'rgba(255, 255, 255, 0.02)' }}>
                    <th style={{ padding: '1rem 1.25rem', color: 'var(--text-secondary)', fontSize: '0.8rem', textTransform: 'uppercase' }}>Assessment</th>
                    <th style={{ padding: '1rem 1.25rem', color: 'var(--text-secondary)', fontSize: '0.8rem', textTransform: 'uppercase' }}>Date & Time</th>
                    <th style={{ padding: '1rem 1.25rem', color: 'var(--text-secondary)', fontSize: '0.8rem', textTransform: 'uppercase' }}>Status</th>
                    <th style={{ padding: '1rem 1.25rem', color: 'var(--text-secondary)', fontSize: '0.8rem', textTransform: 'uppercase' }}>Integrations Included</th>
                    <th style={{ padding: '1rem 1.25rem', color: 'var(--text-secondary)', fontSize: '0.8rem', textTransform: 'uppercase' }}>Evidence Items</th>
                    <th style={{ padding: '1rem 1.25rem', color: 'var(--text-secondary)', fontSize: '0.8rem', textTransform: 'uppercase', textAlign: 'right' }}>Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {assessments.map((a: any) => {
                    const isDone = a.status === 'completed' || a.status === 'partial';
                    const runIntegrations = (a.runs || []).map((r: any) => r.integration);
                    const totalEvidence = (a.runs || []).reduce((sum: number, r: any) => sum + (r.evidence_count || 0), 0);
                    const dateStr = a.created_at || a.started_at;

                    return (
                      <tr key={a.id} style={{ borderBottom: '1px solid var(--border-color)' }}>
                        <td style={{ padding: '1.25rem' }}>
                          <div style={{ fontWeight: 600, color: 'var(--text-primary)' }}>{a.name || 'Compliance Assessment'}</div>
                          <div style={{ fontSize: '0.75rem', color: 'var(--text-secondary)', fontFamily: 'monospace', marginTop: '0.2rem' }}>
                            ID: {a.id.slice(0, 8)}...{a.id.slice(-6)}
                          </div>
                        </td>
                        <td style={{ padding: '1.25rem', fontSize: '0.875rem' }}>
                          {dateStr ? `${new Date(dateStr).toLocaleDateString()} ${new Date(dateStr).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}` : 'N/A'}
                        </td>
                        <td style={{ padding: '1.25rem' }}>
                          <span className={`badge ${isDone ? 'badge-success' : a.status === 'failed' ? 'badge-danger' : 'badge-warning'}`}>
                            {a.status}
                          </span>
                        </td>
                        <td style={{ padding: '1.25rem' }}>
                          <div className="flex gap-1" style={{ flexWrap: 'wrap' }}>
                            {runIntegrations.length > 0 ? (
                              runIntegrations.map((intName: string) => (
                                <span
                                  key={intName}
                                  className="badge"
                                  style={{
                                    backgroundColor: 'rgba(59, 130, 246, 0.15)',
                                    color: '#60a5fa',
                                    border: '1px solid rgba(59, 130, 246, 0.3)',
                                    textTransform: 'uppercase',
                                    fontSize: '0.7rem',
                                  }}
                                >
                                  {intName.replace('_', ' ')}
                                </span>
                              ))
                            ) : (
                              <span style={{ color: 'var(--text-secondary)', fontSize: '0.8rem' }}>None</span>
                            )}
                          </div>
                        </td>
                        <td style={{ padding: '1.25rem', fontSize: '0.875rem' }}>
                          <strong>{totalEvidence}</strong> items
                        </td>
                        <td style={{ padding: '1.25rem', textAlign: 'right' }}>
                          <div className="flex gap-2" style={{ justifyContent: 'flex-end' }}>
                            <button
                              className="btn btn-primary"
                              style={{ padding: '0.4rem 0.8rem', fontSize: '0.8rem' }}
                              onClick={() => viewAssessmentReport(a)}
                            >
                              View Report
                            </button>
                            <button
                              className="btn btn-secondary"
                              style={{ padding: '0.4rem 0.8rem', fontSize: '0.8rem' }}
                              title="Export PDF"
                              onClick={() => handleExportPDF('combined', a.id)}
                            >
                              PDF
                            </button>
                            <button
                              className="btn btn-secondary"
                              style={{ padding: '0.4rem 0.8rem', fontSize: '0.8rem' }}
                              title="Export JSON"
                              onClick={() => handleExportJSON('combined', a.id)}
                            >
                              JSON
                            </button>
                          </div>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </div>
        )}
      </div>
    );
  };

  /* ────────────────────────────────────────────────────────
   * MAIN COMPONENT RETURN: AUTH VS MAIN DASHBOARD
   * ──────────────────────────────────────────────────────── */

  if (!token) {
    return renderAuth();
  }

  return (
    <div className="flex" style={{ minHeight: '100vh' }}>
      {/* Sidebar */}
      <aside
        style={{
          width: '250px',
          backgroundColor: 'var(--glass-bg)',
          borderRight: '1px solid var(--border-color)',
          padding: '1.5rem',
          display: 'flex',
          flexDirection: 'column',
          gap: '2rem',
        }}
      >
        <div>
          <h2 style={{ color: 'var(--accent-primary)', fontSize: '1.25rem' }}>DPDP Scanner</h2>
          <p style={{ fontSize: '0.75rem', color: 'var(--text-secondary)' }}>V2</p>
        </div>
        <nav className="flex-col gap-2">
          {['dashboard', 'new_assessment', 'status', 'assessments'].map((tab) => {
            const isTabActive = activeTab === tab || (tab === 'new_assessment' && activeTab === 'integrations');
            return (
              <button
                key={tab}
                onClick={() => setActiveTab(tab)}
                style={{
                  background: isTabActive ? 'rgba(59, 130, 246, 0.1)' : 'transparent',
                  color: isTabActive ? 'var(--text-primary)' : 'var(--text-secondary)',
                  border: 'none',
                  borderLeft: isTabActive ? '3px solid var(--accent-primary)' : '3px solid transparent',
                  padding: '0.75rem 1rem',
                  textAlign: 'left',
                  cursor: 'pointer',
                  textTransform: 'capitalize',
                  width: '100%',
                }}
              >
                {tab.replace('_', ' ')}
              </button>
            );
          })}
        </nav>
        <div style={{ marginTop: 'auto' }}>
          <button onClick={handleLogout} className="btn btn-secondary" style={{ width: '100%', color: 'var(--danger)' }}>
            Sign Out
          </button>
        </div>
      </aside>

      {/* Main Content Area */}
      <main style={{ flex: 1, padding: '2rem', height: '100vh', overflowY: 'auto' }}>
        <header className="flex justify-between items-center" style={{ marginBottom: '2rem' }}>
          <div>
            <h1 className="animate-fade-in" style={{ textTransform: 'capitalize' }}>
              {(activeTab === 'new_assessment' || activeTab === 'integrations') ? 'New Assessment' : activeTab.replace('_', ' ')}
            </h1>
            <p className="animate-fade-in" style={{ color: 'var(--text-secondary)' }}>
              {clientName}
            </p>
          </div>
          {activeTab !== 'new_assessment' && activeTab !== 'integrations' && (
            <button className="btn btn-primary" onClick={() => setActiveTab('new_assessment')}>
              New Assessment
            </button>
          )}
        </header>

        {errorMsg && (
          <div
            style={{
              marginBottom: '1rem',
              padding: '0.75rem 1rem',
              background: 'rgba(239, 68, 68, 0.15)',
              border: '1px solid var(--danger)',
              color: 'var(--danger)',
            }}
          >
            {errorMsg}
          </div>
        )}

        {activeTab === 'dashboard' && renderDashboard()}
        {(activeTab === 'new_assessment' || activeTab === 'integrations') && renderNewAssessment()}
        {activeTab === 'status' && renderStatusReport()}
        {activeTab === 'assessments' && renderAssessmentHistory()}
      </main>

      {/* Human Review Modal */}
      {overrideModalOpen && overrideFinding && (
        <div style={{
          position: 'fixed', top: 0, left: 0, right: 0, bottom: 0,
          backgroundColor: 'rgba(0,0,0,0.7)', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 1000
        }}>
          <div style={{
            background: 'var(--bg-secondary)', padding: '2rem', borderRadius: 'var(--radius-lg)', width: '500px', maxWidth: '90%'
          }}>
            <h2 style={{ marginTop: 0, marginBottom: '1rem', color: 'var(--text-primary)' }}>Override Finding Verdict</h2>
            <p style={{ color: 'var(--text-secondary)', marginBottom: '1.5rem', fontSize: '0.9rem' }}>
              You are reviewing a finding that requires human intervention. Please provide the final verdict and a mandatory reason for this change.
            </p>
            
            <div style={{ marginBottom: '1rem' }}>
              <label style={{ display: 'block', marginBottom: '0.5rem', fontWeight: 500, color: 'var(--text-primary)' }}>Original Verdict</label>
              <div style={{ padding: '0.5rem', background: 'rgba(255,255,255,0.05)', borderRadius: '4px', color: 'var(--text-secondary)' }}>
                {overrideFinding.verdict}
              </div>
            </div>

            <div style={{ marginBottom: '1rem' }}>
              <label style={{ display: 'block', marginBottom: '0.5rem', fontWeight: 500, color: 'var(--text-primary)' }}>New Verdict</label>
              <select 
                className="input-field" 
                value={overrideVerdict} 
                onChange={(e) => setOverrideVerdict(e.target.value)}
              >
                <option value="Observed">Observed (Compliant)</option>
                <option value="Not observed">Not observed (Non-compliant)</option>
                <option value="Cannot determine">Cannot determine</option>
              </select>
            </div>

            <div style={{ marginBottom: '1.5rem' }}>
              <label style={{ display: 'block', marginBottom: '0.5rem', fontWeight: 500, color: 'var(--text-primary)' }}>Reason for Override (Required)</label>
              <textarea 
                className="input-field" 
                rows={3}
                placeholder="Explain why this verdict is being overridden..."
                value={overrideReason}
                onChange={(e) => setOverrideReason(e.target.value)}
              ></textarea>
            </div>

            <div className="flex justify-end gap-2">
              <button 
                className="btn btn-secondary" 
                onClick={() => {
                  setOverrideModalOpen(false);
                  setOverrideFinding(null);
                  setOverrideReason('');
                }}
              >
                Cancel
              </button>
              <button 
                className="btn btn-primary" 
                onClick={submitOverride}
                disabled={!overrideReason.trim() || overrideVerdict.toUpperCase().replace(' ', '_') === overrideFinding.verdict.toUpperCase().replace(' ', '_')}
              >
                Submit Override
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

export default App;
