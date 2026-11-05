<!doctype html>
<html lang="en">
<head>
    <meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
    <meta name="csrf-token" content="{{ csrf_token() }}">
    <title>@yield('title', 'WorkTrack') | WorkTrack</title>
    <script>try { const t=localStorage.getItem('geosite-theme')==='dark'?'dark':'light'; document.documentElement.dataset.theme=t; document.documentElement.dataset.bsTheme=t; } catch(e) { console.warn('Theme unavailable',e); }</script>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">
    <link href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" rel="stylesheet">
    <link href="{{ asset('static/css/app.css') }}" rel="stylesheet">
    <link href="{{ asset('static/css/hostinger.css') }}" rel="stylesheet">
    @yield('head')
</head>
<body class="{{ auth()->check() ? 'workspace '.(auth()->user()->role === 'admin' ? 'admin-workspace' : 'employee-workspace') : 'guest-workspace' }}">
<a class="skip-link" href="#main-content">Skip to content</a>
@auth
<aside class="{{ auth()->user()->role === 'admin' ? 'offcanvas' : 'offcanvas-lg' }} offcanvas-start workspace-sidebar" tabindex="-1" id="workspaceNav" aria-label="WorkTrack navigation">
    <div class="offcanvas-header"><strong>WorkTrack</strong><button class="btn-close" data-bs-dismiss="offcanvas" data-bs-target="#workspaceNav" aria-label="Close navigation"></button></div>
    <div class="offcanvas-body d-flex flex-column p-3">
        <a class="workspace-brand mb-4" href="/">WorkTrack</a>
        <span class="small mb-3 sidebar-identity">{{ auth()->user()->name }} / {{ auth()->user()->designation }}</span>
        @php
            $links = auth()->user()->role === 'admin'
                ? ['/admin'=>'Dashboard', '/admin/employees'=>'Employees', '/admin/sites'=>'Site', '/admin/projects'=>'Projects', '/admin/assignments'=>'Assignments', '/admin/attendance'=>'Attendance', '/admin/work-status'=>'Work confirmations', '/admin/leaves'=>'Leave', '/reports'=>'Reports', '/submitted-reports'=>'Submitted PDFs', '/admin/invoices'=>'Invoices & POs', '/admin/referrals'=>'Referrals', '/admin/settings'=>'Settings']
                : ['/employee'=>'Home', '/employee/leaves'=>'My leave', '/reports'=>'Reports', '/submitted-reports'=>'Submitted PDFs', '/employee/referrals'=>'Referrals'];
        @endphp
        <nav class="nav flex-column gap-1">
            @foreach($links as $href => $label)
            <a class="workspace-link {{ request()->is(ltrim($href,'/')) ? 'active' : '' }}" href="{{ url($href) }}" aria-label="{{ $label }}" title="{{ $label }}"><svg width="18" height="18" viewBox="0 0 18 18" aria-hidden="true"><rect x="3" y="3" width="12" height="12" rx="3" fill="none" stroke="currentColor" stroke-width="2"/></svg><span>{{ $label }}</span></a>
            @endforeach
        </nav>
        <form method="post" action="{{ url('/logout') }}" class="mt-4">@csrf<button class="btn btn-outline-secondary w-100">Sign out</button></form>
    </div>
</aside>
@endauth
<div class="workspace-main">
    <header class="workspace-topbar">
        <div class="topbar-context">@auth
            @if(auth()->user()->role === 'admin')<button class="btn btn-outline-primary" type="button" data-bs-toggle="offcanvas" data-bs-target="#workspaceNav">Open navigation</button>
            @else<button class="btn btn-outline-primary" id="sidebar-toggle" type="button" aria-controls="workspaceNav" aria-expanded="true">Navigation</button>@endif
        @endauth <a href="{{ url('/') }}" class="workspace-brand">@if(\Illuminate\Support\Facades\DB::table('app_settings')->where('id',1)->value('brand_logo_filename'))<img src="{{ url('/brand-logo') }}" alt="Company logo" style="width:36px;height:36px;object-fit:contain">@endif WorkTrack</a></div>
        <button id="theme-toggle" class="btn btn-outline-secondary" aria-label="Toggle dark mode">Theme</button>
    </header>
    <main class="container-fluid workspace-content px-3 px-lg-4 py-4" id="main-content" tabindex="-1">
        @if(session('success'))<div class="alert alert-success" role="status">{{ session('success') }}</div>@endif
        @if(session('warning'))<div class="alert alert-warning" role="alert">{{ session('warning') }}</div>@endif
        @if(session('error'))<div class="alert alert-danger" role="alert">{{ session('error') }}</div>@endif
        @if($errors->any())<div class="alert alert-danger" role="alert"><ul class="mb-0">@foreach($errors->all() as $error)<li>{{ $error }}</li>@endforeach</ul></div>@endif
        @auth
        @if(auth()->user()->role === 'employee')
            @php($tracking = \Illuminate\Support\Facades\DB::table('attendance')->where('employee_id',auth()->id())->where('status','onsite')->first())
            @if($tracking)<div id="visit-location-status" class="alert alert-info small" data-endpoint="{{ url('/api/visit/'.$tracking->id.'/location') }}" role="status">Location updates require this page to remain open and visible.</div>@endif
        @endif
        @endauth
        @yield('content')
    </main>
    <footer class="workspace-footer">WorkTrack &middot; A better-connected workday &middot; {{ date('Y') }}</footer>
</div>
@if(auth()->check() && auth()->user()->role === 'employee')
<nav class="mobile-dock" aria-label="Quick navigation">
    @foreach(['/employee'=>'Home','/employee/leaves'=>'Leave','/reports'=>'Reports','/submitted-reports'=>'PDFs'] as $href=>$label)
    <a href="{{ url($href) }}" @if(request()->is(ltrim($href,'/'))) class="active" aria-current="page" @endif><span>{{ $label }}</span></a>
    @endforeach
</nav>
@endif
@if(auth()->check() && auth()->user()->role === 'employee' && (session('employee_welcome_pending') || session('employee_thank_you_pending')))
<dialog class="robot-welcome" id="employee-welcome" aria-labelledby="robot-welcome-title" aria-describedby="robot-welcome-message">
    <svg class="welcome-robot" viewBox="0 0 240 220" aria-hidden="true"><g class="robot-float"><rect x="57" y="35" width="126" height="101" rx="32" fill="#8d7af1"/><rect x="69" y="47" width="102" height="73" rx="23" fill="#27234e"/><g fill="#aaf3e4"><circle cx="95" cy="80" r="9"/><circle cx="145" cy="80" r="9"/></g><path d="M108 103Q120 113 132 103" fill="none" stroke="#aaf3e4" stroke-width="5"/><rect x="83" y="143" width="74" height="45" rx="18" fill="#8d7af1"/></g></svg>
    <h2 id="robot-welcome-title">{{ session('employee_thank_you_pending') ? 'Thank you' : 'Hello' }}, {{ explode(' ', auth()->user()->name)[0] }}!</h2>
    <p id="robot-welcome-message">{{ session('employee_thank_you_pending') ? 'Your site report has been submitted. Thank you for your hard work!' : "What are you doing today? Let's make it a great day!" }}</p>
    <p id="robot-voice-status" role="status">Starting robot voice. Tap Play voice or skip the robot.</p>
    <button class="btn btn-outline-primary" id="robot-play-voice">Play voice</button> <button class="btn btn-primary" id="robot-skip">Skip robot</button>
</dialog>
<script src="{{ asset('static/js/employee-welcome.js') }}"></script>
@endif
<script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/js/bootstrap.bundle.min.js"></script>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<script src="{{ asset('static/js/app.js') }}"></script>
@if(isset($tracking) && $tracking)<script src="{{ asset('static/js/location-tracking.js') }}"></script>@endif
@yield('scripts')
@stack('scripts')
</body>
</html>
